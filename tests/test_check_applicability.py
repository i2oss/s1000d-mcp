"""Unit tests for check_applicability against the sample ACT.

samples/corpus/ has two modules with applicProperty assertions (both APU
inlet-filter procedures assert apuOption="Fitted"; the remove procedure
also asserts engineVariant="TurboProp") that are all valid against
schemas/s1000d_mcp_sample_act.xml. samples/applicability-invalid/ has two
modules that are schema-valid but semantically wrong: one asserts an
attribute the ACT doesn't define at all, the other asserts a real
attribute with a value outside its allowed set.
"""

from s1000d_mcp.server import check_applicability

APU_REMOVE_FILTER = "MERM100-A-049-00-00AA-00A-040A-A"
APU_INSTALL_FILTER = "MERM100-A-049-00-00AA-00A-041A-A"


def test_clean_corpus_has_no_violations():
    result = check_applicability("samples/corpus")
    assert result["module_count"] == 7
    assert result["violations"] == []
    assert result["parse_errors"] == []


def test_clean_corpus_assertions_are_attached_to_the_right_modules():
    result = check_applicability("samples/corpus")
    by_dmc = {m["dmc"]: m["assertions"] for m in result["modules"]}

    assert {"ident": "apuOption", "value": "Fitted", "status": "ok"} in by_dmc[
        APU_REMOVE_FILTER
    ]
    assert {"ident": "engineVariant", "value": "TurboProp", "status": "ok"} in by_dmc[
        APU_REMOVE_FILTER
    ]
    assert by_dmc[APU_INSTALL_FILTER] == [
        {"ident": "apuOption", "value": "Fitted", "status": "ok"}
    ]

    # most corpus modules assert nothing -- that's valid, not a violation
    modules_with_no_assertions = [a for a in by_dmc.values() if a == []]
    assert len(modules_with_no_assertions) == 5


def test_act_attributes_are_loaded_and_exposed():
    result = check_applicability("samples/corpus")
    assert result["act_attributes"]["engineVariant"] == ["Piston", "TurboProp"]
    assert result["act_attributes"]["apuOption"] == ["Fitted", "NotFitted"]


def test_unknown_attribute_and_invalid_value_are_both_flagged():
    result = check_applicability("samples/applicability-invalid")
    assert result["module_count"] == 2

    by_ident = {v["ident"]: v for v in result["violations"]}
    assert by_ident["hydraulicOption"]["status"] == "unknown_attribute"
    assert by_ident["engineVariant"]["status"] == "invalid_value"
    assert by_ident["engineVariant"]["value"] == "Turbojet"


def test_missing_directory_reports_a_parse_error_instead_of_raising():
    result = check_applicability("samples/does-not-exist")
    assert result["module_count"] == 0
    assert result["parse_errors"][0]["message"] == "Directory not found"


def test_missing_act_reports_a_parse_error_instead_of_raising():
    result = check_applicability("samples/corpus", act_path="schemas/does-not-exist.xml")
    assert result["module_count"] == 0
    assert "Failed to load ACT" in result["parse_errors"][0]["message"]
