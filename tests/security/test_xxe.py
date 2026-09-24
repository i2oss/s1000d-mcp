"""Security regression tests: XXE / external-entity hardening.

These pin the fact that the server's XML parser resolves no external
entities and makes no network access. The Phase 1 red team confirmed the
server is not vulnerable today (lxml's modern defaults are safe), so these
tests exist to catch a *regression* -- a future edit or dependency bump
that silently re-enables entity resolution.

Each test asserts two things:
  1. the server's parse blocks the attack (canary file is not read), and
  2. a deliberately misconfigured parser DOES read it -- proving the
     attack file is real and the test would fail loudly if the server's
     parser were ever weakened.

Run directly against the tool function; no network and no API key needed.
"""

from __future__ import annotations

import json

import lxml.etree as etree
import pytest

from s1000d_mcp.server import BASE_DIR, _safe_parser, validate_xml_schema

CANARY = "XXE-CANARY-do-not-leak-8b21"


@pytest.fixture()
def canary_file(tmp_path, monkeypatch):
    """A secret file plus a data module whose external entity points at it,
    both inside a temp BASE_DIR so the path boundary doesn't reject them."""
    monkeypatch.setattr("s1000d_mcp.server.BASE_DIR", tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text(CANARY)
    dm = tmp_path / "xxe.xml"
    dm.write_text(
        '<?xml version="1.0"?>\n'
        f'<!DOCTYPE dmodule [ <!ENTITY xxe SYSTEM "file://{secret}"> ]>\n'
        '<dmodule xmlns="urn:s1000d-mcp:schema-subset:2026">&xxe;</dmodule>\n'
    )
    return secret, dm


def test_file_read_entity_is_not_resolved(canary_file):
    _secret, dm = canary_file
    result = validate_xml_schema(str(dm))
    # The document is rejected as not well-formed (entity undefined); the
    # canary must never appear anywhere in the structured response.
    assert result["valid"] is False
    assert CANARY not in json.dumps(result)


def test_control_proves_the_attack_is_real(canary_file):
    """Sanity control: with entity resolution ON, the same file leaks the
    canary. If this ever stops leaking, the attack file is stale and the
    test above is meaningless."""
    secret, dm = canary_file
    unsafe = etree.XMLParser(resolve_entities=True, no_network=False, load_dtd=True)
    doc = etree.parse(str(dm), parser=unsafe)
    assert CANARY in etree.tostring(doc, encoding="unicode")


def test_safe_parser_does_not_expand_external_entities(tmp_path):
    """Behavioral check on the hardened parser itself: a file-read entity is
    not expanded into the document text."""
    secret = tmp_path / "s.txt"
    secret.write_text(CANARY)
    dm = tmp_path / "d.xml"
    dm.write_text(
        '<?xml version="1.0"?>\n'
        f'<!DOCTYPE r [ <!ENTITY e SYSTEM "file://{secret}"> ]>\n'
        "<r>&e;</r>\n"
    )
    try:
        doc = etree.parse(str(dm), parser=_safe_parser())
    except etree.XMLSyntaxError:
        return  # rejecting outright is also a pass
    assert CANARY not in etree.tostring(doc, encoding="unicode")
