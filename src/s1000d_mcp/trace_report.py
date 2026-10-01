"""Read a JSONL trace file and print a per-tool summary.

The no-backend "dashboard" for Phase 7: instead of standing up a collector
and a web UI, the file exporter writes one span per line and this reads it
back into the numbers that actually matter for an MCP server -- how often
each tool is called, how slow it is at the tail, how often it fails, and
what the LLM-backed tool costs.

    $ s1000d-mcp-trace-report [path]          # default: s1000d-mcp-traces.jsonl
    $ s1000d-mcp-trace-report traces.jsonl --json

Latency percentiles use nearest-rank on the per-tool duration samples.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from typing import Any


def _percentile(samples: list[float], pct: float) -> float:
    """Nearest-rank percentile of a list of samples (pct in 0..100)."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    rank = math.ceil(pct / 100 * len(ordered))
    rank = max(1, min(rank, len(ordered)))
    return ordered[rank - 1]


def load_spans(path: str) -> list[dict[str, Any]]:
    """Load span records from a JSONL file, skipping any malformed line."""
    spans: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                spans.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return spans


def summarize_traces(path: str) -> dict[str, Any]:
    """Aggregate a JSONL trace file into per-tool and cost statistics.

    Returns a dict with:
      - total_calls
      - tools: {tool_name: {calls, errors, error_rate,
                p50_ms, p95_ms, p99_ms, max_ms}}
      - cost: {calls, input_tokens, output_tokens, total_cost_usd,
               by_model: {model: {...}}}
    Only ``tool.*`` spans contribute to the per-tool table; LLM usage is read
    from whichever span carries ``llm.*`` attributes.
    """
    durations: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)

    llm_calls = 0
    llm_input = 0
    llm_output = 0
    llm_cost = 0.0
    by_model: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    )

    for span in load_spans(path):
        name = span.get("name", "")
        attrs = span.get("attributes", {}) or {}

        if name.startswith("tool."):
            tool = attrs.get("mcp.tool.name") or name[len("tool."):]
            durations[tool].append(float(span.get("duration_ms", 0.0)))
            if attrs.get("mcp.tool.result_status") in ("error", "exception"):
                errors[tool] += 1

        if "llm.model" in attrs:
            model = attrs["llm.model"]
            llm_calls += 1
            in_tok = int(attrs.get("llm.input_tokens", 0) or 0)
            out_tok = int(attrs.get("llm.output_tokens", 0) or 0)
            cost = float(attrs.get("llm.cost_usd", 0.0) or 0.0)
            llm_input += in_tok
            llm_output += out_tok
            llm_cost += cost
            m = by_model[model]
            m["calls"] += 1
            m["input_tokens"] += in_tok
            m["output_tokens"] += out_tok
            m["cost_usd"] += cost

    tools: dict[str, Any] = {}
    total_calls = 0
    for tool, samples in sorted(durations.items()):
        calls = len(samples)
        total_calls += calls
        err = errors.get(tool, 0)
        tools[tool] = {
            "calls": calls,
            "errors": err,
            "error_rate": (err / calls) if calls else 0.0,
            "p50_ms": round(_percentile(samples, 50), 3),
            "p95_ms": round(_percentile(samples, 95), 3),
            "p99_ms": round(_percentile(samples, 99), 3),
            "max_ms": round(max(samples), 3),
        }

    return {
        "total_calls": total_calls,
        "tools": tools,
        "cost": {
            "calls": llm_calls,
            "input_tokens": llm_input,
            "output_tokens": llm_output,
            "total_cost_usd": round(llm_cost, 6),
            "by_model": {
                model: {
                    "calls": v["calls"],
                    "input_tokens": v["input_tokens"],
                    "output_tokens": v["output_tokens"],
                    "cost_usd": round(v["cost_usd"], 6),
                }
                for model, v in sorted(by_model.items())
            },
        },
    }


def format_report(summary: dict[str, Any]) -> str:
    """Render the summary as a fixed-width text table."""
    lines: list[str] = []
    lines.append(f"MCP tool activity  ({summary['total_calls']} calls)")
    lines.append("-" * 72)
    header = f"{'tool':<28}{'calls':>6}{'err%':>7}{'p50':>8}{'p95':>8}{'p99':>8}"
    lines.append(header)
    lines.append("-" * 72)
    for tool, s in summary["tools"].items():
        lines.append(
            f"{tool:<28}{s['calls']:>6}{s['error_rate'] * 100:>6.0f}%"
            f"{s['p50_ms']:>8.1f}{s['p95_ms']:>8.1f}{s['p99_ms']:>8.1f}"
        )
    cost = summary["cost"]
    lines.append("")
    lines.append(
        f"LLM usage (suggest_fix): {cost['calls']} calls, "
        f"{cost['input_tokens']} in + {cost['output_tokens']} out tokens, "
        f"${cost['total_cost_usd']:.4f} est."
    )
    for model, m in cost["by_model"].items():
        lines.append(
            f"  {model}: {m['calls']} calls, ${m['cost_usd']:.4f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="s1000d-mcp-trace-report",
        description="Summarize an S1000D MCP JSONL trace file.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="s1000d-mcp-traces.jsonl",
        help="JSONL trace file (default: s1000d-mcp-traces.jsonl)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit the raw summary as JSON."
    )
    args = parser.parse_args(argv)

    try:
        summary = summarize_traces(args.path)
    except FileNotFoundError:
        print(f"trace file not found: {args.path}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(format_report(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
