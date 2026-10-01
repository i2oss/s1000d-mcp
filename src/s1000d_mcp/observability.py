"""Observability for the S1000D MCP server (Phase 7).

One OpenTelemetry span per tool call -- tool name, input/output size,
duration, and error type -- plus token and cost accounting on the single
tool that spends money (``suggest_fix``). Everything exports to a local
file or to stderr; there is no network backend to stand up, which keeps
the suite reproducible in CI and on a laptop and never risks corrupting
the MCP stdio stream.

Design notes
------------
* **stdio safety.** This server speaks JSON-RPC over stdout. An exporter
  that writes to stdout would corrupt that stream, so the console exporter
  targets ``stderr`` and the default exporter writes to a JSONL file.
* **Zero-cost when unconfigured.** The module-level tracer is OpenTelemetry's
  default no-op until :func:`configure_tracing` installs a real provider, so
  importing the server (e.g. in another process, or a test that doesn't care
  about traces) emits nothing and writes no files.
* **Minimal touch.** Tools are wrapped with :func:`instrument_tool`, which
  preserves the wrapped function's signature and annotations so the MCP
  SDK still builds identical input/output schemas. The security-critical
  tool bodies are not modified; ``suggest_fix`` gains a single
  :func:`record_llm_usage` call to attach token/cost data to its span.

Configuration (environment variables)
--------------------------------------
* ``S1000D_MCP_OTEL_EXPORTER`` -- ``file`` (default), ``console``, or
  ``none``/``off`` to disable.
* ``S1000D_MCP_TRACE_FILE`` -- JSONL output path for the file exporter
  (default ``s1000d-mcp-traces.jsonl`` in the current working directory).
"""

from __future__ import annotations

import functools
import json
import os
import sys
import threading
from typing import Any, Callable, TypeVar

from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

__all__ = [
    "configure_tracing",
    "instrument_tool",
    "record_llm_usage",
    "tracer",
    "MODEL_PRICES_USD_PER_MTOK",
    "estimate_cost_usd",
]

INSTRUMENTATION_NAME = "s1000d-mcp"

# A single module-level tracer. Until configure_tracing() installs a real
# provider this resolves to the API's no-op tracer, so spans are cheap and
# nothing is written.
tracer = trace.get_tracer(INSTRUMENTATION_NAME)

_configured = False
_global_provider_set = False
_provider: TracerProvider | None = None
_configure_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Cost model
# --------------------------------------------------------------------------- #

# Indicative list prices in USD per million tokens (input, output). These are
# used only to turn a span's recorded token counts into an approximate cost
# for the dashboard/report; they are not billing-accurate and are easy to
# update. A model absent from this table records tokens but a null cost.
MODEL_PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-5": (3.00, 15.00),
}


def estimate_cost_usd(
    model: str, input_tokens: int, output_tokens: int
) -> float | None:
    """Approximate the USD cost of one API call from its token counts.

    Returns ``None`` for a model not in :data:`MODEL_PRICES_USD_PER_MTOK`
    so the caller can record "tokens known, cost unknown" rather than a
    misleading zero.
    """
    price = MODEL_PRICES_USD_PER_MTOK.get(model)
    if price is None:
        return None
    price_in, price_out = price
    return (input_tokens / 1_000_000) * price_in + (output_tokens / 1_000_000) * price_out


# --------------------------------------------------------------------------- #
# JSONL file exporter
# --------------------------------------------------------------------------- #


def _span_to_record(span: ReadableSpan) -> dict[str, Any]:
    """Flatten a finished span to a compact, self-describing JSON record.

    One line per span: the tool name, a millisecond duration derived from
    the span's own start/end, its status, and every attribute we attached.
    This is the format the trace-report summarizer reads back.
    """
    ctx = span.get_span_context()
    start_ns = span.start_time or 0
    end_ns = span.end_time or start_ns
    return {
        "name": span.name,
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "start_time_ns": start_ns,
        "end_time_ns": end_ns,
        "duration_ms": (end_ns - start_ns) / 1_000_000,
        "status": span.status.status_code.name,
        "attributes": dict(span.attributes or {}),
    }


class JsonlFileSpanExporter(SpanExporter):
    """Write each finished span as one JSON object per line to a file.

    A tiny, dependency-free sink: no collector, no network, just an
    append-only JSONL log that is trivial to tail, diff in a test, or feed
    to the summarizer. Writes are serialized under a lock and flushed per
    batch so a reader always sees whole lines.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()

    def export(self, spans) -> SpanExportResult:
        try:
            lines = [json.dumps(_span_to_record(s), default=str) for s in spans]
            with self._lock:
                with open(self._path, "a", encoding="utf-8") as fh:
                    for line in lines:
                        fh.write(line + "\n")
            return SpanExportResult.SUCCESS
        except OSError:
            # Telemetry must never take down the server: a failed export is
            # dropped, not raised.
            return SpanExportResult.FAILURE

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def shutdown(self) -> None:
        return None


# --------------------------------------------------------------------------- #
# Provider setup
# --------------------------------------------------------------------------- #


def _build_exporter() -> SpanExporter | None:
    """Select a span exporter from the environment.

    ``none``/``off`` disables tracing entirely. ``console`` prints spans to
    *stderr* (never stdout -- that is the MCP transport). ``file`` (the
    default) appends JSONL to ``S1000D_MCP_TRACE_FILE``.
    """
    choice = os.environ.get("S1000D_MCP_OTEL_EXPORTER", "file").strip().lower()
    if choice in ("none", "off", ""):
        return None
    if choice == "console":
        # out=stderr so span dumps don't corrupt the JSON-RPC stdio stream.
        return ConsoleSpanExporter(out=sys.stderr)
    # Default: file.
    path = os.environ.get("S1000D_MCP_TRACE_FILE", "s1000d-mcp-traces.jsonl")
    return JsonlFileSpanExporter(path)


def configure_tracing(force: bool = False) -> bool:
    """Install a real TracerProvider with the configured exporter.

    Idempotent: safe to call more than once (only the first call installs a
    provider) unless ``force=True`` is passed, which rebuilds the provider --
    used by tests that switch exporters between cases. Returns True when
    tracing is active afterwards, False when it was disabled via the
    environment.

    Uses a SimpleSpanProcessor (export on span end) rather than a batch
    processor so a short-lived process -- a CI scan, a single tool call in a
    test -- flushes its spans without needing an explicit shutdown.

    The module-level :data:`tracer` is driven from a provider instance we
    hold directly, rather than relying solely on the global provider. OTel
    only lets the global provider be set once per process, so holding our own
    instance is what lets ``force=True`` rebuild tracing (e.g. tests that
    switch exporters) and keeps instrumentation working regardless of what
    else in the process may have claimed the global provider.
    """
    global _configured, _global_provider_set, _provider, tracer
    with _configure_lock:
        if _configured and not force:
            return _provider is not None

        exporter = _build_exporter()
        if exporter is None:
            # Disabled: drop any provider we held and fall back to the API's
            # no-op tracer so nothing is emitted.
            if _provider is not None:
                _provider.shutdown()
            _provider = None
            tracer = trace.get_tracer(INSTRUMENTATION_NAME)
            _configured = True
            return False

        if _provider is not None:
            _provider.shutdown()
        _provider = TracerProvider()
        _provider.add_span_processor(SimpleSpanProcessor(exporter))

        # Best effort: publish as the process-wide provider the first time, so
        # other OTel-aware libraries share it. OTel forbids overriding it
        # later, so subsequent (re)configurations just update our own tracer.
        if not _global_provider_set:
            trace.set_tracer_provider(_provider)
            _global_provider_set = True

        tracer = _provider.get_tracer(INSTRUMENTATION_NAME)
        _configured = True
        return True


# --------------------------------------------------------------------------- #
# Instrumentation helpers
# --------------------------------------------------------------------------- #


def _byte_len(obj: Any) -> int:
    """Best-effort serialized size of a tool's input or output, in bytes.

    Used only as a span attribute (how much data moved through a call), so
    an un-serializable value falls back to its ``repr`` length rather than
    failing the call.
    """
    try:
        return len(json.dumps(obj, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return len(repr(obj).encode("utf-8"))


def _reports_error(result: Any) -> bool:
    """Decide whether a tool's structured result signals an operational
    failure, as opposed to a normal finding.

    These tools never raise; they return dicts. A failure is an explicit
    ``ok: False`` or a top-level ``error`` key. A ``valid: False`` document
    or a non-empty ``errors``/``violations`` list is a *successful* scan that
    found problems -- that is the tool working, not failing -- so it is not
    counted as an error here.
    """
    if not isinstance(result, dict):
        return False
    if result.get("ok") is False:
        return True
    if "error" in result:
        return True
    return False


F = TypeVar("F", bound=Callable[..., Any])


def instrument_tool(fn: F) -> F:
    """Wrap a tool function in a span named ``tool.<fn.__name__>``.

    Records input size, output size, duration (via the span's own clock),
    and -- on an unexpected exception -- the error type. ``functools.wraps``
    plus following ``__wrapped__`` means the MCP SDK still reads the original
    signature and annotations, so the generated tool schema is unchanged.
    """
    tool_name = fn.__name__

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with tracer.start_as_current_span(f"tool.{tool_name}") as span:
            span.set_attribute("mcp.tool.name", tool_name)
            # Record the input size without storing argument values (which
            # can contain file contents or user text) in the trace.
            payload = kwargs if kwargs else {"args": args}
            span.set_attribute("mcp.tool.input_bytes", _byte_len(payload))
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:  # defensive: tools shouldn't raise
                span.set_attribute("mcp.tool.result_status", "exception")
                span.set_attribute("mcp.tool.error_type", type(exc).__name__)
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                span.record_exception(exc)
                raise
            span.set_attribute("mcp.tool.output_bytes", _byte_len(result))
            span.set_attribute(
                "mcp.tool.result_status",
                "error" if _reports_error(result) else "ok",
            )
            return result

    return wrapper  # type: ignore[return-value]


def record_llm_usage(model: str, usage: Any) -> None:
    """Attach token counts and an estimated cost to the current span.

    Called from ``suggest_fix`` (the only tool that calls the Anthropic API)
    with the response's ``usage`` object. No-ops safely when there is no
    recording span or no usage object, so it never affects the tool's
    behavior or its hardened error handling.
    """
    span = trace.get_current_span()
    if span is None or not span.is_recording() or usage is None:
        return
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    span.set_attribute("llm.model", model)
    span.set_attribute("llm.input_tokens", input_tokens)
    span.set_attribute("llm.output_tokens", output_tokens)
    span.set_attribute("llm.total_tokens", input_tokens + output_tokens)
    cost = estimate_cost_usd(model, input_tokens, output_tokens)
    if cost is not None:
        span.set_attribute("llm.cost_usd", round(cost, 6))
