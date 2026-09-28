"""Unit tests for generate_data_module_skeleton."""

from lxml import etree

from s1000d_mcp.server import DEFAULT_SCHEMA_PATH, generate_data_module_skeleton

VALID_DMC = dict(
    modelIdentCode="MERM100",
    systemDiffCode="A",
    systemCode="049",
    subSystemCode="0",
    subSubSystemCode="0",
    assyCode="02AA",
    disassyCode="00",
    disassyCodeVariant="A",
    infoCode="520",
    infoCodeVariant="B",
    itemLocationCode="A",
)


def test_procedural_skeleton_is_schema_valid():
    result = generate_data_module_skeleton(
        VALID_DMC, "APU Starter", "Remove Procedure", content_type="procedural"
    )
    assert result["valid"] is True
    assert result["errors"] == []
    assert result["dmc"] == "MERM100-A-049-00-02AA-00A-520B-A"
    # the XML it returns really does parse and match the schema, not just
    # a self-reported flag
    schema = etree.XMLSchema(etree.parse(str(DEFAULT_SCHEMA_PATH)))
    assert schema.validate(etree.fromstring(result["xml"].encode("utf-8")))


def test_descriptive_skeleton_is_schema_valid():
    result = generate_data_module_skeleton(
        VALID_DMC, "APU Starter", "Description", content_type="descriptive"
    )
    assert result["valid"] is True
    assert "<description>" in result["xml"]
    assert "<procedure>" not in result["xml"]


def test_invalid_content_type_is_rejected_without_raising():
    result = generate_data_module_skeleton(VALID_DMC, "x", "y", content_type="bogus")
    assert result["valid"] is False
    assert result["xml"] is None
    assert "bogus" in result["errors"][0]["message"]


def test_missing_dmc_part_is_rejected_without_raising():
    incomplete = dict(VALID_DMC)
    del incomplete["infoCode"]
    result = generate_data_module_skeleton(incomplete, "x", "y")
    assert result["valid"] is False
    assert "infoCode" in result["errors"][0]["message"]


def test_malformed_dmc_part_surfaces_as_a_schema_error_not_an_exception():
    bad = dict(VALID_DMC)
    bad["systemCode"] = "BAD"
    result = generate_data_module_skeleton(bad, "x", "y")
    assert result["valid"] is False
    assert result["xml"] is not None  # it still generated and self-checked it
    assert any("systemCode" in e["message"] for e in result["errors"])


def test_special_characters_in_title_are_escaped():
    result = generate_data_module_skeleton(VALID_DMC, "Fuel & Air System", "Test <check>", content_type="descriptive")
    assert result["valid"] is True
    assert "Fuel &amp; Air System" in result["xml"]


def test_write_to_file_then_refuses_to_overwrite_without_flag(tmp_path, monkeypatch):
    import s1000d_mcp.server as server_module

    # Point the tools' allowed base directory at the temp folder. (Was
    # REPO_ROOT; the path-boundary hardening resolves writes against
    # BASE_DIR, so tests set BASE_DIR to keep writes inside the sandbox.)
    monkeypatch.setattr(server_module, "BASE_DIR", tmp_path)
    output_path = "generated/skeleton.XML"

    first = generate_data_module_skeleton(
        VALID_DMC, "APU Starter", "Remove Procedure", output_path=output_path
    )
    assert first["file"] == "generated/skeleton.XML"
    assert (tmp_path / output_path).exists()

    second = generate_data_module_skeleton(
        VALID_DMC, "APU Starter", "Remove Procedure", output_path=output_path
    )
    assert second["file"] is None
    assert any("already exists" in e["message"] for e in second["errors"])

    third = generate_data_module_skeleton(
        VALID_DMC,
        "APU Starter",
        "Remove Procedure",
        output_path=output_path,
        overwrite=True,
    )
    assert third["file"] == "generated/skeleton.XML"
