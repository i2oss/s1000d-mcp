"""Security regression suite: every Phase 1 red-team attack, asserted to fail.

This is the "re-run every attack and confirm it now fails" capstone. Each
test reproduces a finding from the Phase 1 red team and asserts the
hardened server now rejects it safely -- no file escape, no leak, no crash.

All run against the tool functions directly; no network, no API key.
"""

from __future__ import annotations

import json

import pytest

import s1000d_mcp.server as server
from s1000d_mcp.server import (
    check_applicability,
    check_cross_references,
    generate_data_module_skeleton,
    suggest_fix,
    validate_xml_schema,
)

VALID_DM = "samples/corpus/DMC-MERM100-A-049-00-00-00AA-040A-A_001-00_EN-US.XML"
VALID_DMC = {
    "modelIdentCode": "MERM100", "systemDiffCode": "A", "systemCode": "049",
    "subSystemCode": "0", "subSubSystemCode": "0", "assyCode": "00",
    "disassyCode": "00", "disassyCodeVariant": "A", "infoCode": "040",
    "infoCodeVariant": "A", "itemLocationCode": "A",
}


def _blob(result) -> str:
    return json.dumps(result)


# --- Path traversal: read (every path parameter) -----------------------------

@pytest.mark.parametrize("payload", ["/etc/hostname", "../../../../etc/hostname"])
def test_validate_dm_path_traversal_rejected(payload):
    r = validate_xml_schema(payload)
    assert "outside the allowed directory" in _blob(r)


def test_validate_schema_path_traversal_rejected():
    r = validate_xml_schema(VALID_DM, schema_path="/etc/hostname")
    assert "outside the allowed directory" in _blob(r)


def test_cross_references_directory_traversal_rejected():
    r = check_cross_references("/etc")
    assert "outside the allowed directory" in _blob(r)


def test_applicability_directory_and_act_traversal_rejected():
    assert "outside the allowed directory" in _blob(check_applicability("/etc"))
    r = check_applicability("samples/corpus", act_path="../../../../etc/hostname")
    assert "outside the allowed directory" in _blob(r)


def test_suggest_fix_dm_path_traversal_rejected():
    r = suggest_fix("../../../../etc/hostname", {"m": "x"})
    assert r["ok"] is False
    assert "outside the allowed directory" in r["reason"]


# --- Path traversal: write / overwrite ---------------------------------------

def test_skeleton_write_escape_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "BASE_DIR", tmp_path)
    victim = tmp_path.parent / "victim.txt"
    victim.write_text("ORIGINAL")
    r = generate_data_module_skeleton(
        VALID_DMC, "APU", "Remove", output_path="../victim.txt", overwrite=True
    )
    assert any("outside the allowed directory" in e["message"] for e in r["errors"])
    assert victim.read_text() == "ORIGINAL"  # not overwritten


# --- Existence oracle: present vs missing must be indistinguishable -----------

def test_existence_oracle_closed():
    # A present and a missing host path are both refused at the boundary with
    # the same kind of error -- the response no longer distinguishes "exists"
    # from "does not exist" (the message only echoes the caller's own input).
    present = validate_xml_schema("/etc/hostname")["errors"][0]["message"]
    missing = validate_xml_schema("/etc/definitely-not-here-xyz")["errors"][0]["message"]
    assert "outside the allowed directory" in present
    assert "outside the allowed directory" in missing
    assert "not found" not in present and "not found" not in missing


# --- XXE ---------------------------------------------------------------------

def test_xxe_file_read_entity_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "BASE_DIR", tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("XXE-SECRET-2f9c")
    dm = tmp_path / "xxe.XML"
    dm.write_text(
        '<?xml version="1.0"?>\n'
        f'<!DOCTYPE d [ <!ENTITY x SYSTEM "file://{secret}"> ]>\n'
        '<dmodule xmlns="urn:s1000d-mcp:schema-subset:2026">&x;</dmodule>\n'
    )
    r = validate_xml_schema(str(dm))
    assert r["valid"] is False
    assert "XXE-SECRET-2f9c" not in _blob(r)


# --- DoS: oversized input ----------------------------------------------------

def test_oversized_file_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "BASE_DIR", tmp_path)
    monkeypatch.setattr(server, "MAX_FILE_BYTES", 1000)
    big = tmp_path / "big.XML"
    big.write_text("<dmodule xmlns='urn:s1000d-mcp:schema-subset:2026'>"
                   + "<para>x</para>" * 500 + "</dmodule>")
    r = validate_xml_schema(str(big))
    assert "too large" in _blob(r)


def test_oversized_string_param_rejected(monkeypatch):
    monkeypatch.setattr(server, "MAX_STR_LEN", 100)
    r = generate_data_module_skeleton(VALID_DMC, "A" * 500, "Remove")
    assert any("too long" in e["message"] for e in r["errors"])


# --- Error leakage: no host paths / tracebacks in responses ------------------

def test_error_responses_do_not_leak_host_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "BASE_DIR", tmp_path)
    bad = tmp_path / "bad.XML"
    bad.write_text("not xml <<<")
    for result in (
        validate_xml_schema(VALID_DM, schema_path=str(bad.relative_to(tmp_path))),
        check_applicability("samples/corpus", act_path=str(bad.relative_to(tmp_path))),
    ):
        blob = _blob(result)
        assert "Traceback" not in blob
        for marker in ("/root/", "/home/", "/Users/", "/opt/", "/srv/"):
            assert marker not in blob


# --- suggest_fix: model allowlist (cost abuse) -------------------------------

def test_disallowed_model_rejected():
    r = suggest_fix(VALID_DM, {"m": "x"}, model="claude-super-expensive-99")
    assert r["ok"] is False
    assert "not in the allowed set" in r["reason"]
