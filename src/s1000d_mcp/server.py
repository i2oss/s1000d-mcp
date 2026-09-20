"""S1000D MCP server.

Exposes tools for reviewing S1000D-style data modules. Week 1 added the
server itself and a `ping` health check. Week 2 added `validate_xml_schema`,
which checks a data module against this project's subset XSD (see
schemas/s1000d_mcp_subset.xsd for why it's a subset and not one of the
official S1000D schemas) and returns structured, line-numbered errors.
Week 3 added `check_cross_references`, which parses a directory of data
modules, builds a DMC-keyed reference graph from their `dmRef` /
`graphicRef` content, and flags dangling references (a `dmRef` whose
target DMC isn't in the directory) and orphaned modules (nothing in the
directory references them). Week 4 adds `generate_data_module_skeleton`
(scaffold a new, schema-valid empty module from DMC parts and a title --
self-validated against the same subset XSD before it's returned) and
`check_applicability`, which checks a data module's <applicProperty>
assertions against a sample Applicability Cross-reference Table (ACT;
see schemas/s1000d_mcp_sample_act.xml).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

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

ACT_NS = "urn:s1000d-mcp:schema-subset:2026:act"
ACT_NSMAP = {"act": ACT_NS}
DEFAULT_ACT_PATH = REPO_ROOT / "schemas" / "s1000d_mcp_sample_act.xml"


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


DMC_PART_NAMES = (
    "modelIdentCode",
    "systemDiffCode",
    "systemCode",
    "subSystemCode",
    "subSubSystemCode",
    "assyCode",
    "disassyCode",
    "disassyCodeVariant",
    "infoCode",
    "infoCodeVariant",
    "itemLocationCode",
)


@mcp.tool()
def generate_data_module_skeleton(
    dmc: dict[str, str],
    tech_name: str,
    info_name: str,
    content_type: str = "procedural",
    issue_number: str = "001",
    in_work: str = "00",
    output_path: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Scaffold a new, schema-valid empty data module from a template.

    Args:
        dmc: The eleven DMC part attributes -- modelIdentCode,
            systemDiffCode, systemCode, subSystemCode, subSubSystemCode,
            assyCode, disassyCode, disassyCodeVariant, infoCode,
            infoCodeVariant, itemLocationCode -- plus optional
            languageIsoCode (default "en") and countryIsoCode (default
            "US").
        tech_name: Technical name for the dmTitle (e.g. "Auxiliary Power
            Unit").
        info_name: Info name for the dmTitle (e.g. "Remove Procedure").
        content_type: "procedural" (default) or "descriptive" -- selects
            which empty content skeleton to generate.
        issue_number: Defaults to "001" (first issue).
        in_work: Defaults to "00".
        output_path: If given, write the generated module to this path
            (resolved against the repo root). Refused if the file already
            exists unless overwrite=True.
        overwrite: Allow replacing an existing file at output_path.

    Returns:
        A dict with:
          - xml: the generated data module, as a string
          - dmc: its canonical DMC key (same format used by
            check_cross_references)
          - filename: a suggested S1000D-style filename
          - file: the path written to, or None if output_path wasn't given
          - valid: whether the generated module passes schema validation
            against the default subset schema (a self-check -- this
            should always be True for well-formed inputs)
          - errors: schema errors, if any
    """
    if content_type not in ("procedural", "descriptive"):
        return {
            "xml": None,
            "dmc": None,
            "filename": None,
            "file": None,
            "valid": False,
            "errors": [
                {
                    "line": None,
                    "column": None,
                    "level": "fatal",
                    "message": (
                        "content_type must be 'procedural' or 'descriptive', "
                        f"got {content_type!r}"
                    ),
                }
            ],
        }

    missing = [part for part in DMC_PART_NAMES if part not in dmc]
    if missing:
        return {
            "xml": None,
            "dmc": None,
            "filename": None,
            "file": None,
            "valid": False,
            "errors": [
                {
                    "line": None,
                    "column": None,
                    "level": "fatal",
                    "message": f"dmc is missing required part(s): {', '.join(missing)}",
                }
            ],
        }

    language_iso_code = dmc.get("languageIsoCode", "en")
    country_iso_code = dmc.get("countryIsoCode", "US")
    today = date.today()

    if content_type == "procedural":
        content_xml = (
            "    <procedure>\n"
            "      <mainProcedure>\n"
            "        <proceduralStep>\n"
            "          <para>TODO: describe the first step.</para>\n"
            "        </proceduralStep>\n"
            "      </mainProcedure>\n"
            "    </procedure>\n"
        )
    else:
        content_xml = (
            "    <description>\n"
            "      <para>TODO: describe this item.</para>\n"
            "    </description>\n"
        )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<dmodule xmlns="{S1KDMCP_NS}">
  <identAndStatusSection>
    <dmAddress>
      <dmIdent>
        <dmCode modelIdentCode="{dmc['modelIdentCode']}" systemDiffCode="{dmc['systemDiffCode']}" systemCode="{dmc['systemCode']}"
                 subSystemCode="{dmc['subSystemCode']}" subSubSystemCode="{dmc['subSubSystemCode']}" assyCode="{dmc['assyCode']}"
                 disassyCode="{dmc['disassyCode']}" disassyCodeVariant="{dmc['disassyCodeVariant']}" infoCode="{dmc['infoCode']}"
                 infoCodeVariant="{dmc['infoCodeVariant']}" itemLocationCode="{dmc['itemLocationCode']}"/>
        <language languageIsoCode="{language_iso_code}" countryIsoCode="{country_iso_code}"/>
        <issueInfo issueNumber="{issue_number}" inWork="{in_work}"/>
      </dmIdent>
      <dmAddressItems>
        <issueDate year="{today.year:04d}" month="{today.month:02d}" day="{today.day:02d}"/>
        <dmTitle>
          <techName>{escape(tech_name)}</techName>
          <infoName>{escape(info_name)}</infoName>
        </dmTitle>
      </dmAddressItems>
    </dmAddress>
    <dmStatus issueType="new">
      <security securityClassification="01"/>
      <responsiblePartnerCompany enterpriseCode="MRD01">Meridian Aerospace (fictional)</responsiblePartnerCompany>
      <originator enterpriseCode="MRD01">Meridian Aerospace (fictional)</originator>
      <applic>
        <displayText>TODO: state applicability</displayText>
      </applic>
      <qualityAssurance>
        <unverified/>
      </qualityAssurance>
    </dmStatus>
  </identAndStatusSection>
  <content>
{content_xml}  </content>
</dmodule>
"""

    parsed = etree.fromstring(xml.encode("utf-8"))
    dmcode_el = parsed.find(
        "s1kdmcp:identAndStatusSection/s1kdmcp:dmAddress/"
        "s1kdmcp:dmIdent/s1kdmcp:dmCode",
        NSMAP,
    )
    dmc_key = _dmc_key(dmcode_el)
    filename = (
        f"DMC-{dmc['modelIdentCode']}-{dmc['systemDiffCode']}-{dmc['systemCode']}-"
        f"{dmc['subSystemCode']}{dmc['subSubSystemCode']}-{dmc['assyCode']}-"
        f"{dmc['disassyCode']}{dmc['disassyCodeVariant']}-"
        f"{dmc['infoCode']}{dmc['infoCodeVariant']}-{dmc['itemLocationCode']}"
        f"_{issue_number}-{in_work}_{language_iso_code}-{country_iso_code}.XML"
    )

    schema = _load_schema(DEFAULT_SCHEMA_PATH)
    is_valid = schema.validate(parsed)
    errors = [
        {
            "line": entry.line,
            "column": entry.column,
            "level": entry.level_name.lower(),
            "message": entry.message,
        }
        for entry in schema.error_log
    ]

    written_path = None
    if output_path:
        target = _resolve(output_path)
        if target.exists() and not overwrite:
            return {
                "xml": xml,
                "dmc": dmc_key,
                "filename": filename,
                "file": None,
                "valid": is_valid,
                "errors": errors
                + [
                    {
                        "line": None,
                        "column": None,
                        "level": "fatal",
                        "message": f"{target} already exists; pass overwrite=True to replace it.",
                    }
                ],
            }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(xml, encoding="utf-8")
        written_path = _rel(target)

    return {
        "xml": xml,
        "dmc": dmc_key,
        "filename": filename,
        "file": written_path,
        "valid": is_valid,
        "errors": errors,
    }


@mcp.tool()
def check_applicability(directory: str, act_path: str | None = None) -> dict[str, Any]:
    """Check a directory of data modules' applicability assertions against
    a sample Applicability Cross-reference Table (ACT).

    Each data module may declare zero or more structured applicability
    assertions as <applicProperty applicPropertyIdent="..."
    applicPropertyValue="..."/> inside its <applic> element (in addition
    to the always-required free-text <displayText>). This tool checks
    each assertion's identifier and value against the ACT's defined
    product attributes and their allowed values.

    Args:
        directory: Path to a directory of data module XML files, resolved
            against the repo root if relative (e.g. "samples/corpus").
        act_path: Path to an ACT XML file, resolved against the repo root
            if relative. Defaults to schemas/s1000d_mcp_sample_act.xml.

    Returns:
        A dict with:
          - act: the resolved ACT path used
          - directory: the resolved directory that was scanned
          - module_count: number of modules found
          - modules: [{dmc, file, assertions: [{ident, value, status}]}]
            for every module, where status is one of "ok",
            "unknown_attribute" (the ident isn't in the ACT at all), or
            "invalid_value" (the ident is known but the value isn't among
            its allowed values). A module with no assertions gets an
            empty list -- that's valid (applicability can be stated only
            as free text) and is not itself a violation.
          - violations: the same non-"ok" entries flattened across all
            modules, each with dmc and file added, for convenient
            iteration
          - act_attributes: {ident: [allowed values]} as loaded from the
            ACT, for reference
          - parse_errors: [{file, message}] for the ACT or any data
            module that couldn't be parsed; scanning continues past
            these rather than raising
    """
    dir_path = _resolve(directory)
    act_file = _resolve(act_path) if act_path else DEFAULT_ACT_PATH

    empty_result: dict[str, Any] = {
        "act": _rel(act_file),
        "directory": _rel(dir_path),
        "module_count": 0,
        "modules": [],
        "violations": [],
        "act_attributes": {},
        "parse_errors": [],
    }

    if not dir_path.is_dir():
        empty_result["parse_errors"].append(
            {"file": str(dir_path), "message": "Directory not found"}
        )
        return empty_result

    try:
        act_doc = etree.parse(str(act_file))
    except (OSError, etree.XMLSyntaxError) as exc:
        empty_result["parse_errors"].append(
            {"file": _rel(act_file), "message": f"Failed to load ACT: {exc}"}
        )
        return empty_result

    attributes: dict[str, set[str]] = {}
    for attr_el in act_doc.getroot().findall("act:productAttribute", ACT_NSMAP):
        ident = attr_el.get("applicPropertyIdent")
        values = {
            value_el.get("applicPropertyValue")
            for value_el in attr_el.findall("act:value", ACT_NSMAP)
        }
        attributes[ident] = values

    modules: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
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
        assertions: list[dict[str, str]] = []

        for prop_el in root.findall(
            "s1kdmcp:identAndStatusSection/s1kdmcp:dmStatus/"
            "s1kdmcp:applic/s1kdmcp:applicProperty",
            NSMAP,
        ):
            ident = prop_el.get("applicPropertyIdent")
            value = prop_el.get("applicPropertyValue")
            if ident not in attributes:
                status = "unknown_attribute"
            elif value not in attributes[ident]:
                status = "invalid_value"
            else:
                status = "ok"

            entry = {"ident": ident, "value": value, "status": status}
            assertions.append(entry)
            if status != "ok":
                violations.append({"dmc": key, "file": _rel(xml_file), **entry})

        modules.append({"dmc": key, "file": _rel(xml_file), "assertions": assertions})

    return {
        "act": _rel(act_file),
        "directory": _rel(dir_path),
        "module_count": len(modules),
        "modules": modules,
        "violations": violations,
        "act_attributes": {ident: sorted(values) for ident, values in attributes.items()},
        "parse_errors": parse_errors,
    }


def main() -> None:
    """Entry point for the `s1000d-mcp` console script (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
