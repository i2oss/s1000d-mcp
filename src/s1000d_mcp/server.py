"""S1000D MCP server.

Exposes tools for reviewing S1000D-style data modules. Week 1 added the
server itself and a `ping` health check; week 2 adds real work:
`validate_xml_schema`, which checks a data module against this project's
subset XSD (see schemas/s1000d_mcp_subset.xsd for why it's a subset and
not one of the official S1000D schemas) and returns structured,
line-numbered errors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lxml import etree
from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    name="s1000d-mcp",
    version="0.1.0",
    instructions=(
        "Tools for reviewing S1000D data modules: schema validation, "
        "cross-reference checking, applicability checking, and "
        "LLM-assisted fix suggestions. Currently in early development."
    ),
)

# src/s1000d_mcp/server.py -> src/s1000d_mcp -> src -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_PATH = REPO_ROOT / "schemas" / "s1000d_mcp_subset.xsd"


@mcp.tool()
def ping(message: str = "hello from s1000d-mcp") -> str:
    """Health-check tool: echoes a message back with a server tag.

    Used to confirm the server is reachable and a tool call round-trips
    correctly, before real S1000D tooling is added.
    """
    return f"pong: {message}"


def _resolve(path_str: str) -> Path:
    """Resolve a possibly-relative path against the repo root."""
    path = Path(path_str)
    return path if path.is_absolute() else REPO_ROOT / path


def _load_schema(schema_path: Path) -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(schema_path)))


@mcp.tool()
def validate_xml_schema(dm_path: str, schema_path: str | None = None) -> dict[str, Any]:
    """Validate a data module XML file against the S1000D subset XSD.

    Args:
        dm_path: Path to the data module XML file to validate. Relative
            paths are resolved against the repository root (e.g.
            "samples/valid/DMC-....XML").
        schema_path: Optional path to an alternate XSD to validate
            against, also resolved against the repo root if relative.
            Defaults to schemas/s1000d_mcp_subset.xsd.

    Returns:
        A dict with:
          - file: the resolved path that was checked
          - schema: the resolved schema path used
          - valid: True if the file conforms to the schema
          - errors: a list of {line, column, level, message} entries,
            one per schema violation (empty when valid is True)
    """
    target = _resolve(dm_path)
    xsd_path = _resolve(schema_path) if schema_path else DEFAULT_SCHEMA_PATH

    if not target.exists():
        return {
            "file": str(target),
            "schema": str(xsd_path),
            "valid": False,
            "errors": [
                {
                    "line": None,
                    "column": None,
                    "level": "fatal",
                    "message": f"File not found: {target}",
                }
            ],
        }

    try:
        schema = _load_schema(xsd_path)
    except (etree.XMLSchemaParseError, OSError) as exc:
        return {
            "file": str(target),
            "schema": str(xsd_path),
            "valid": False,
            "errors": [
                {
                    "line": None,
                    "column": None,
                    "level": "fatal",
                    "message": f"Schema failed to load: {exc}",
                }
            ],
        }

    try:
        doc = etree.parse(str(target))
    except etree.XMLSyntaxError as exc:
        return {
            "file": str(target),
            "schema": str(xsd_path),
            "valid": False,
            "errors": [
                {
                    "line": exc.lineno,
                    "column": exc.offset,
                    "level": "fatal",
                    "message": f"XML is not well-formed: {exc.msg}",
                }
            ],
        }

    is_valid = schema.validate(doc)
    errors = [
        {
            "line": entry.line,
            "column": entry.column,
            "level": entry.level_name.lower(),
            "message": entry.message,
        }
        for entry in schema.error_log
    ]

    return {
        "file": str(target),
        "schema": str(xsd_path),
        "valid": is_valid,
        "errors": errors,
    }


def main() -> None:
    """Entry point for the `s1000d-mcp` console script (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
