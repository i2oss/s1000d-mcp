"""Unit tests for the validate_xml_schema tool against the sample corpus.

Calls the tool function directly (not over the MCP protocol -- that
round-trip is covered separately by tests/test_server_e2e.py) so these
stay fast and give precise assertions on the structured error output.
"""

from pathlib import Path

import pytest

from s1000d_mcp.server import REPO_ROOT, validate_xml_schema

VALID_DM = "samples/corpus/DMC-MERM100-A-049-00-00-00AA-040A-A_001-00_EN-US.XML"
BAD_DMC_ORDER_DM = "samples/schema-invalid/DMC-MERM100-A-BAD-00-00-00AA-040A-A_001-00_EN-US.XML"
BAD_ENUM_DM = "samples/schema-invalid/DMC-MERM100-A-049-00-00-00AA-041A-A_001-00_EN-US.XML"


def test_valid_sample_passes_with_no_errors():
    result = validate_xml_schema(VALID_DM)
    assert result["valid"] is True
    assert result["errors"] == []


def test_bad_dmc_and_element_order_is_rejected():
    result = validate_xml_schema(BAD_DMC_ORDER_DM)
    assert result["valid"] is False
    assert len(result["errors"]) >= 1
    first = result["errors"][0]
    assert first["line"] == 6
    assert "language" in first["message"]
    assert "dmCode" in first["message"]


def test_bad_enum_missing_element_and_bad_date_are_all_reported():
    result = validate_xml_schema(BAD_ENUM_DM)
    assert result["valid"] is False
    messages = " ".join(e["message"] for e in result["errors"])
    lines = {e["line"] for e in result["errors"]}

    # three distinct, independent violations should all surface
    assert "13" in messages and "pattern" in messages  # bad issueDate month
    assert "final" in messages and "enumeration" in messages  # bad issueType
    assert "security" in messages  # missing required element
    assert len(lines) == 3


def test_missing_file_reports_a_fatal_error_instead_of_raising():
    result = validate_xml_schema("samples/schema-invalid/does-not-exist.XML")
    assert result["valid"] is False
    assert result["errors"][0]["level"] == "fatal"
    assert "not found" in result["errors"][0]["message"].lower()


def test_default_schema_path_resolves_under_repo_root():
    result = validate_xml_schema(VALID_DM)
    assert result["schema"] == str(REPO_ROOT / "schemas" / "s1000d_mcp_subset.xsd")


@pytest.mark.parametrize(
    "sample_path",
    [
        p.relative_to(REPO_ROOT)
        for p in (REPO_ROOT / "samples").rglob("*.XML")
    ],
    ids=lambda p: str(p),
)
def test_every_sample_in_the_corpus_parses_without_raising(sample_path: Path):
    # Whatever the sample's validity, the tool must never raise -- it
    # always returns a structured result.
    result = validate_xml_schema(str(sample_path))
    assert isinstance(result["valid"], bool)
    assert isinstance(result["errors"], list)
