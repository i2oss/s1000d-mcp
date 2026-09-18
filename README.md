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
workflow via a `SKILL.md` — using a hand-built subset schema modeled on S1000D's
publicly documented structure (the official XSDs are only distributed through a
registered-user portal, not a plain download — see [Schema](#schema) below) and
entirely hand-built, non-proprietary sample data modules. No proprietary or
work-related content is used anywhere in this repository.

## Tools

| Tool | Status | Purpose |
|---|---|---|
| `validate_xml_schema` | ✅ implemented | Validate a data module against the project's subset S1000D XSD; return structured errors with line numbers. |
| `check_cross_references` | ✅ implemented | Parse a directory of data modules, build a DMC-keyed reference graph from their content, flag dangling references and orphaned modules. |
| `generate_data_module_skeleton` | planned | Scaffold a new, schema-valid empty data module from a template, given DMC parts, info code, and title. |
| `check_applicability` | planned | Validate applicability annotations against a sample Applicability Cross-reference Table (ACT). |
| `suggest_fix` | planned | Given a validation error and its surrounding XML context, call the Anthropic API (with an S1000D-authoring-rules system prompt) for a suggested corrected snippet and explanation. |

On top of the individual tools, a `review-data-module` `SKILL.md` chains them into one
workflow: validate → check cross-references → check applicability → `suggest_fix` for
each failure → summarize findings in a report.

## Schema

The official S1000D XSDs are distributed only through the S1000D Council's
registered-user portal (`users.s1000d.org`) — free to register, but not a
plain public download, so this repo can't redistribute or auto-fetch them.
Instead, [`schemas/s1000d_mcp_subset.xsd`](schemas/s1000d_mcp_subset.xsd) is a
hand-built schema that models the real structural shape of a data module
(DMC, language/issue metadata, title, status/security/applicability/BREX
reference, QA, and descriptive/procedural content with inline `dmRef` /
`graphicRef` cross-references) closely enough to exercise genuine schema
validation — required elements, element order, attribute patterns, and
enumerations — against realistic sample data. It is **not** one of the
official S1000D schemas; closing that gap (or adding a mode that points at a
real, user-supplied schema set) is a roadmap item.

The sample corpus in [`samples/`](samples/) is entirely fictional: a made-up
aircraft ("Meridian M100"), its auxiliary power unit, and a small fuel
subsystem. Three directories, each serving a different tool's tests:

- `samples/corpus/` — 7 schema-valid, interlinked data modules (an APU
  description, its remove/install inlet-filter procedures, an electrical
  interface description, a deliberately standalone maintenance-schedule
  description, and a two-module fuel-system pair that cross-references
  back into the APU description). Used by both `validate_xml_schema`
  (all pass) and `check_cross_references` (0 dangling references; 3
  modules come out orphaned because nothing in the corpus points to them).
- `samples/schema-invalid/` — 2 modules deliberately broken in different
  ways (bad DMC pattern + wrong element order; invalid enumeration +
  missing required element + malformed date), for `validate_xml_schema`'s
  failure path.
- `samples/broken-refs/` — a copy of the clean corpus with two `dmRef`
  targets deliberately pointed at DMCs that don't exist in the directory
  (one same-system, one cross-system), for `check_cross_references`'s
  failure path. Still fully schema-valid — a dangling reference is a
  semantic problem, not a schema violation.

### Example

```
$ uv run python -c "
from s1000d_mcp.server import validate_xml_schema
import json
print(json.dumps(validate_xml_schema(
    'samples/invalid/DMC-MERM100-A-BAD-00-00-00AA-040A-A_001-00_EN-US.XML'
), indent=2))
"
{
  "file": "/.../samples/invalid/DMC-MERM100-A-BAD-00-00-00AA-040A-A_001-00_EN-US.XML",
  "schema": "/.../schemas/s1000d_mcp_subset.xsd",
  "valid": false,
  "errors": [
    {
      "line": 6,
      "column": 0,
      "level": "error",
      "message": "Element 'language': This element is not expected. Expected is ( dmCode )."
    }
  ]
}
```

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

- [x] `validate_xml_schema` (schema validation)
- [x] `check_cross_references`
- [ ] `generate_data_module_skeleton`
- [ ] `check_applicability`
- [ ] `suggest_fix`
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
