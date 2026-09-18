"""Unit tests for check_cross_references against the clean corpus and the
deliberately reference-broken copy of it.

samples/corpus/ is a hand-built, interlinked set of 7 schema-valid data
modules for the fictional Meridian M100 aircraft: an APU description (A),
its remove/install inlet-filter procedures (B, C), an APU
electrical-interface description (D), a deliberately standalone APU
maintenance-schedule description (E), and a small fuel-system pair (F, G)
that cross-references back into the APU description. See each file's
<dmTitle> for the human-readable topic.

samples/broken-refs/ is a copy of that same corpus with two dmRef targets
deliberately pointed at DMCs that don't exist in the directory -- a
same-system dangling reference (C -> a nonexistent infoCode) and a
cross-system one (F -> a nonexistent modelIdentCode) -- while remaining
fully schema-valid, since a dangling reference isn't a schema violation.
"""

from s1000d_mcp.server import check_cross_references

APU_OVERVIEW = "MERM100-A-049-00-00AA-00A-022A-A"
APU_REMOVE_FILTER = "MERM100-A-049-00-00AA-00A-040A-A"
APU_INSTALL_FILTER = "MERM100-A-049-00-00AA-00A-041A-A"
APU_ELECTRICAL = "MERM100-A-049-00-00AA-00A-023A-A"
APU_MAINT_SCHEDULE = "MERM100-A-049-00-00AA-00A-520A-A"
FUEL_VALVE_DESC = "MERM100-A-028-00-00AA-00A-022A-A"
FUEL_VALVE_SEAL = "MERM100-A-028-00-00AA-00A-040A-A"


def test_clean_corpus_has_no_dangling_references():
    result = check_cross_references("samples/corpus")
    assert result["module_count"] == 7
    assert result["dangling_references"] == []
    assert result["parse_errors"] == []


def test_clean_corpus_orphans_are_exactly_the_unreferenced_modules():
    result = check_cross_references("samples/corpus")
    orphan_dmcs = {m["dmc"] for m in result["orphaned_modules"]}
    # nothing in the corpus points to these three
    assert orphan_dmcs == {APU_ELECTRICAL, APU_MAINT_SCHEDULE, FUEL_VALVE_SEAL}
    # the hub description has several incoming references, so it must not
    # be flagged as an orphan
    assert APU_OVERVIEW not in orphan_dmcs


def test_clean_corpus_edge_count_and_a_specific_edge():
    result = check_cross_references("samples/corpus")
    assert len(result["edges"]) == 8
    assert {"from": APU_REMOVE_FILTER, "to": APU_OVERVIEW} in result["edges"]


def test_clean_corpus_collects_graphic_references_without_checking_them():
    result = check_cross_references("samples/corpus")
    idents = {g["info_entity_ident"] for g in result["graphic_references"]}
    assert "ICN-MERM10012A00-001" in idents
    assert len(result["graphic_references"]) == 4


def test_broken_refs_directory_reports_both_introduced_dangling_references():
    result = check_cross_references("samples/broken-refs")
    assert result["module_count"] == 7

    dangling_by_source = {d["from"]: d["to"] for d in result["dangling_references"]}
    assert len(dangling_by_source) == 2
    assert dangling_by_source[APU_INSTALL_FILTER] == "MERM100-A-049-00-00AA-00A-099A-A"
    assert dangling_by_source[FUEL_VALVE_DESC] == "MERM999-A-049-00-00AA-00A-022A-A"

    # both breaks removed a real edge, so the broken corpus has two fewer
    # resolved edges than the clean one
    assert len(result["edges"]) == 6


def test_missing_directory_reports_a_parse_error_instead_of_raising():
    result = check_cross_references("samples/does-not-exist")
    assert result["module_count"] == 0
    assert result["parse_errors"][0]["message"] == "Directory not found"
