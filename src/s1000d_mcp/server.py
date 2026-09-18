"""S1000D MCP server.

Exposes tools for reviewing S1000D-style data modules. Week 1 added the
server itself and a `ping` health check. Week 2 added `validate_xml_schema`,
which checks a data module against this project's subset XSD (see
schemas/s1000d_mcp_subset.xsd for why it's a subset and not one of the
official S1000D schemas) and returns structured, line-numbered errors.
Week 3 adds `check_cross_references`, which parses a directory of data
modules, builds a DMC-keyed reference graph from their `dmRef` /
`graphicRef` content, and flags dangling references (a `dmRef` whose
target DMC isn't in the directory) and orphaned modules (nothing in the
directory references them).
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

S1KDMCP_NS = "urn:s1000d-mcp:schema-subset:2026"
NSMAP = {"s1kdmcp": S1KDMCP_NS}


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


def _rel(path: Path) -> str:
    """Render a path relative to the repo root when possible, for
    readable tool output; falls back to the absolute path otherwise."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _dmc_key(dm_code_el: etree._Element) -> str:
    """Build a canonical, comparable string key from a <dmCode> element's
    attributes, used both as a module's own identity and to match dmRef
    targets against it."""
    a = dm_code_el.attrib
    return "-".join(
        [
            a["modelIdentCode"],
            a["systemDiffCode"],
            a["systemCode"],
            a["subSystemCode"] + a["subSubSystemCode"],
            a["assyCode"],
            a["disassyCode"] + a["disassyCodeVariant"],
            a["infoCode"] + a["infoCodeVariant"],
            a["itemLocationCode"],
        ]
    )


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


@mcp.tool()
def check_cross_references(directory: str) -> dict[str, Any]:
    """Build a reference graph across a directory of data modules.

    Parses every *.XML file directly in `directory` (resolved against the
    repo root if relative), identifies each module by the DMC in its own
    identAndStatusSection, and follows every inline <dmRef> in its content
    to another module's DMC. (BREX references are a separate governance
    concept and are intentionally not part of this content graph.)

    Args:
        directory: Path to a directory of data module XML files, e.g.
            "samples/corpus" or "samples/broken-refs".

    Returns:
        A dict with:
          - directory: the resolved directory that was scanned
          - module_count: number of modules found
          - modules: [{dmc, file, title}] for every module found
          - edges: [{from, to}] resolved dmRef edges (both ends in this
            directory)
          - dangling_references: [{from, from_file, to}] dmRef targets
            that don't match any module in this directory
          - orphaned_modules: [{dmc, file, title}] modules with zero
            incoming dmRef edges from other modules in this directory
          - graphic_references: [{file, info_entity_ident}] every
            <graphicRef> found (informational -- graphics are external
            assets, not other data modules, so they're listed but not
            checked for existence)
          - parse_errors: [{file, message}] for any file that couldn't be
            parsed or had no recognizable dmCode; scanning continues past
            these rather than raising
    """
    dir_path = _resolve(directory)
    if not dir_path.is_dir():
        return {
            "directory": str(dir_path),
            "module_count": 0,
            "modules": [],
            "edges": [],
            "dangling_references": [],
            "orphaned_modules": [],
            "graphic_references": [],
            "parse_errors": [
                {"file": str(dir_path), "message": "Directory not found"}
            ],
        }

    modules: dict[str, dict[str, Any]] = {}
    graphic_references: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []

    for xml_file in sorted(dir_path.glob("*.XML")):
        try:
            doc = etree.parse(str(xml_file))
        except etree.XMLSyntaxError as exc:
            parse_errors.append({"file": _rel(xml_file), "message": str(exc)})
            continue

        root = doc.getroot()
        dmcode_el = root.find(
            "s1kdmcp:identAndStatusSection/s1kdmcp:dmAddress/"
            "s1kdmcp:dmIdent/s1kdmcp:dmCode",
            NSMAP,
        )
        if dmcode_el is None:
            parse_errors.append(
                {"file": _rel(xml_file), "message": "No dmCode found; skipped"}
            )
            continue

        key = _dmc_key(dmcode_el)
        title_el = root.find(".//s1kdmcp:dmTitle/s1kdmcp:techName", NSMAP)
        title = title_el.text if title_el is not None else None

        outgoing = [
            _dmc_key(ref_dmcode)
            for ref_dmcode in root.findall(
                ".//s1kdmcp:dmRef/s1kdmcp:dmRefIdent/s1kdmcp:dmCode", NSMAP
            )
        ]

        for graphic_ref in root.findall(".//s1kdmcp:graphicRef", NSMAP):
            graphic_references.append(
                {
                    "file": _rel(xml_file),
                    "info_entity_ident": graphic_ref.get("infoEntityIdent"),
                }
            )

        modules[key] = {"file": _rel(xml_file), "title": title, "outgoing": outgoing}

    edges: list[dict[str, str]] = []
    dangling_references: list[dict[str, Any]] = []
    incoming_count: dict[str, int] = {key: 0 for key in modules}

    for key, mod in modules.items():
        for target in mod["outgoing"]:
            if target in modules:
                edges.append({"from": key, "to": target})
                incoming_count[target] = incoming_count.get(target, 0) + 1
            else:
                dangling_references.append(
                    {"from": key, "from_file": mod["file"], "to": target}
                )

    orphaned_modules = [
        {"dmc": key, "file": mod["file"], "title": mod["title"]}
        for key, mod in modules.items()
        if incoming_count.get(key, 0) == 0
    ]

    return {
        "directory": _rel(dir_path),
        "module_count": len(modules),
        "modules": [
            {"dmc": key, "file": mod["file"], "title": mod["title"]}
            for key, mod in sorted(modules.items())
        ],
        "edges": edges,
        "dangling_references": dangling_references,
        "orphaned_modules": orphaned_modules,
        "graphic_references": graphic_references,
        "parse_errors": parse_errors,
    }


def main() -> None:
    """Entry point for the `s1000d-mcp` console script (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
