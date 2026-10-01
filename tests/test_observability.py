"""Phase 7 observability tests.

These pin the telemetry contract so a refactor can't silently stop emitting
spans, drop the input/output-size or error attributes, or break the
token/cost accounting the trace report depends on. They use the file
exporter into a tmp path -- no network, no backend -- and never call the
real Anthropic API (usage is fed through a fake response object).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from s1000d_mcp import observability as obs


def _read_spans(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.fixture
def trace_file(tmp_path, monkeypatch):
    """Point the file exporter at a tmp JSONL file and rebuild the provider."""
    path = tmp_path / "traces.jsonl"
    monkeypatch.setenv("S1000D_MCP_OTEL_EXPORTER", "file")
    monkeypatch.setenv("S1000D_MCP_TRACE_FILE", str(path))
    assert obs.configure_tracing(force=True) is True
    return path


# --------------------------------------------------------------------------- #
# Decorator behavior
# --------------------------------------------------------------------------- #


def test_instrument_tool_preserves_signature_and_name():
    def sample(dm_path: str, count: int = 3) -> dict:
        return {"ok": True}

    wrapped = obs.instrument_tool(sample)
    import inspect

    assert wrapped.__name__ == "sample"
    assert wrapped.__wrapped__ is sample
    # The MCP SDK reads the signature off __wrapped__; it must be intact.
    assert list(inspect.signature(wrapped).parameters) == ["dm_path", "count"]


def test_span_records_sizes_and_ok_status(trace_file):
    @obs.instrument_tool
    def sample(x: str) -> dict:
        return {"ok": True, "echo": x}

    sample(x="hello")

    spans = _read_spans(trace_file)
    assert len(spans) == 1
    attrs = spans[0]["attributes"]
    assert spans[0]["name"] == "tool.sample"
    assert attrs["mcp.tool.name"] == "sample"
    assert attrs["mcp.tool.input_bytes"] > 0
    assert attrs["mcp.tool.output_bytes"] > 0
    assert attrs["mcp.tool.result_status"] == "ok"
    assert spans[0]["duration_ms"] >= 0


def test_ok_false_is_recorded_as_error(trace_file):
    @obs.instrument_tool
    def failing(x: str) -> dict:
        return {"ok": False, "reason": "nope"}

    failing(x="y")
    attrs = _read_spans(trace_file)[0]["attributes"]
    assert attrs["mcp.tool.result_status"] == "error"


def test_finding_is_not_an_error(trace_file):
    # valid=False / non-empty errors is a successful scan that found a
    # problem, not an operational failure -- must stay "ok".
    @obs.instrument_tool
    def scan(x: str) -> dict:
        return {"valid": False, "errors": [{"message": "bad"}]}

    scan(x="y")
    attrs = _read_spans(trace_file)[0]["attributes"]
    assert attrs["mcp.tool.result_status"] == "ok"


def test_exception_is_recorded_and_reraised(trace_file):
    @obs.instrument_tool
    def boom(x: str) -> dict:
        raise ValueError("kaboom")

    with pytest.raises(ValueError):
        boom(x="y")

    span = _read_spans(trace_file)[0]
    attrs = span["attributes"]
    assert attrs["mcp.tool.result_status"] == "exception"
    assert attrs["mcp.tool.error_type"] == "ValueError"
    assert span["status"] == "ERROR"


def test_tool_arguments_are_not_stored_in_the_span(trace_file):
    # Only sizes are recorded, never the argument values -- a file path or
    # document text must not leak into the trace.
    secret = "/home/someone/private/secret-document.xml"

    @obs.instrument_tool
    def sample(dm_path: str) -> dict:
        return {"ok": True}

    sample(dm_path=secret)
    raw = trace_file.read_text()
    assert secret not in raw


# --------------------------------------------------------------------------- #
# Cost model
# --------------------------------------------------------------------------- #


def test_estimate_cost_known_model():
    # haiku 4.5: $1/Mtok in, $5/Mtok out.
    cost = obs.estimate_cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000)
    assert cost == pytest.approx(6.00)


def test_estimate_cost_unknown_model_is_none():
    assert obs.estimate_cost_usd("some-future-model", 100, 100) is None


def test_record_llm_usage_sets_token_and_cost_attributes(trace_file):
    class FakeUsage:
        input_tokens = 2000
        output_tokens = 500

    @obs.instrument_tool
    def suggest_fix(dm_path: str) -> dict:
        obs.record_llm_usage("claude-haiku-4-5", FakeUsage())
        return {"ok": True}

    suggest_fix(dm_path="x")
    attrs = _read_spans(trace_file)[0]["attributes"]
    assert attrs["llm.model"] == "claude-haiku-4-5"
    assert attrs["llm.input_tokens"] == 2000
    assert attrs["llm.output_tokens"] == 500
    assert attrs["llm.total_tokens"] == 2500
    # 2000/1e6*1 + 500/1e6*5 = 0.002 + 0.0025 = 0.0045
    assert attrs["llm.cost_usd"] == pytest.approx(0.0045)


def test_record_llm_usage_is_safe_without_a_span():
    # Called outside any span (no recording span) -- must not raise.
    obs.record_llm_usage("claude-haiku-4-5", None)


# --------------------------------------------------------------------------- #
# Trace report / summarizer
# --------------------------------------------------------------------------- #


def test_summarize_traces_aggregates(trace_file):
    from s1000d_mcp.trace_report import summarize_traces

    @obs.instrument_tool
    def ping(x: str) -> dict:
        return {"ok": True}

    @obs.instrument_tool
    def failing(x: str) -> dict:
        return {"ok": False}

    class FakeUsage:
        input_tokens = 1000
        output_tokens = 200

    @obs.instrument_tool
    def suggest_fix(x: str) -> dict:
        obs.record_llm_usage("claude-sonnet-4-5", FakeUsage())
        return {"ok": True}

    ping(x="a")
    ping(x="b")
    failing(x="c")
    suggest_fix(x="d")

    summary = summarize_traces(str(trace_file))
    assert summary["total_calls"] == 4
    assert summary["tools"]["ping"]["calls"] == 2
    assert summary["tools"]["ping"]["error_rate"] == 0.0
    assert summary["tools"]["failing"]["error_rate"] == 1.0
    # p99 is a real number for each tool
    assert summary["tools"]["ping"]["p99_ms"] >= 0
    cost = summary["cost"]
    assert cost["calls"] == 1
    assert cost["input_tokens"] == 1000
    assert cost["output_tokens"] == 200
    # sonnet 4.5: 1000/1e6*3 + 200/1e6*15 = 0.003 + 0.003 = 0.006
    assert cost["total_cost_usd"] == pytest.approx(0.006)
    assert cost["by_model"]["claude-sonnet-4-5"]["calls"] == 1


def test_percentile_nearest_rank():
    from s1000d_mcp.trace_report import _percentile

    samples = [10, 20, 30, 40, 50]
    assert _percentile(samples, 50) == 30
    assert _percentile(samples, 100) == 50
    assert _percentile([], 95) == 0.0


def test_exporter_none_disables_tracing(monkeypatch):
    monkeypatch.setenv("S1000D_MCP_OTEL_EXPORTER", "none")
    assert obs.configure_tracing(force=True) is False
