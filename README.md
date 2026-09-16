# S1000D MCP Server

An MCP (Model Context Protocol) server that gives an LLM agent a working toolset for
reviewing **S1000D** technical publications — the structured-authoring XML standard
used across aerospace and defense for maintenance and engineering documentation.

## The problem

S1000D content is authored as small, modular XML units called **data modules**, each
identified by a structured **Data Module Code (DMC)**, validated against publicly
published XML schemas, cross-referenced against other data modules and graphics, and
filtered by an **applicability** model that says which content applies to which
product variant or configuration. In a real authoring environment, tools like
Arbortext Editor, Windchill, and DevTrack handle schema validation, cross-reference
integrity, applicability checking, and change-impact tracking as separate, disjoint
steps in someone else's workflow.

This project reimplements the *spirit* of that tooling as a set of MCP tools an LLM
agent can call directly — and then chains those tools into a single agentic review
workflow via a `SKILL.md` — using only public S1000D schema documentation and
entirely hand-built, non-proprietary sample data modules. No proprietary or
work-related content is used anywhere in this repository.

## Planned tools

| Tool | Purpose |
|---|---|
| `validate_xml_schema` | Validate a data module against the public S1000D XSD; return structured errors with line numbers. |
| `check_cross_references` | Parse a directory of data modules, build a reference graph (DMC / graphic references), flag dangling or orphaned references. |
| `generate_data_module_skeleton` | Scaffold a new, schema-valid empty data module from a template, given DMC parts, info code, and title. |
| `check_applicability` | Validate applicability annotations against a sample Applicability Cross-reference Table (ACT). |
| `suggest_fix` | Given a validation error and its surrounding XML context, call the Anthropic API (with an S1000D-authoring-rules system prompt) for a suggested corrected snippet and explanation. |

On top of the individual tools, a `review-data-module` `SKILL.md` chains them into one
workflow: validate → check cross-references → check applicability → `suggest_fix` for
each failure → summarize findings in a report.

## Status

🚧 Early development — see the [roadmap](#roadmap) below and the repo's Issues for
what's in progress.

## Tech stack

- **Language:** Python (3.10+), managed with [uv](https://docs.astral.sh/uv/)
- **XML validation:** [`lxml`](https://lxml.de/) against the public S1000D XSD
- **LLM:** Anthropic API (Claude)
- **MCP:** the official [Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)
- **Testing:** `pytest`, against a small hand-built corpus of sample data modules
  (some valid, some deliberately broken)
- **CI:** GitHub Actions running `pytest` on push

## Setup

```bash
git clone https://github.com/i2oss/s1000d-mcp.git
cd s1000d-mcp
uv sync
```

Run the server:

```bash
uv run s1000d-mcp
```

### Using it with Claude Desktop / Claude Code

Add to your MCP client config (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "s1000d": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/s1000d-mcp", "run", "s1000d-mcp"]
    }
  }
}
```

## Roadmap

- [ ] Core tool set (schema validation, cross-references, skeleton generation,
      applicability, `suggest_fix`)
- [ ] `review-data-module` SKILL.md
- [ ] GitHub Actions CI
- [ ] `v0.1.0` release
- [ ] DITA schema support
- [ ] Simplified Technical English (STE)-style rule linting
- [ ] CLI wrapper

## About

Built by [Ross Shelton](https://rwshelton.com) as a demonstration of agentic AI
development — MCP tooling and Claude `SKILL.md` workflows — applied to a real
technical-documentation problem, drawing on experience authoring S1000D-compliant
content professionally. All sample data modules in this repo are fictional and
non-proprietary.

## License

MIT — see [LICENSE](LICENSE).
