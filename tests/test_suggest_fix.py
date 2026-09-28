"""Unit tests for suggest_fix.

suggest_fix's network-calling seam (_get_anthropic_client /
_call_propose_fix_api) is monkeypatched in every test below except the
live integration test, per the project's "env var + mocked tests"
approach: unit tests never make a real network call or require a real
API key, and CI (which has no key configured) never spends money on a
push. The one live test is skipped automatically unless a real
ANTHROPIC_API_KEY is present in the environment running pytest.
"""

from __future__ import annotations

import os

import anthropic
import pytest

import s1000d_mcp.server as server_module
from s1000d_mcp.server import DEFAULT_MODEL, suggest_fix

VALID_DM = "samples/corpus/DMC-MERM100-A-049-00-00-00AA-040A-A_001-00_EN-US.XML"
BAD_ENUM_DM = "samples/schema-invalid/DMC-MERM100-A-049-00-00-00AA-041A-A_001-00_EN-US.XML"

SAMPLE_ERROR = {
    "line": 12,
    "column": 0,
    "level": "error",
    "message": "Element 'dmStatus': This element is not expected.",
}


class _FakeToolUseBlock:
    def __init__(self, name, input):
        self.type = "tool_use"
        self.name = name
        self.input = input


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeAPIError(anthropic.APIError):
    """A constructible anthropic.APIError for tests -- the real class
    requires an httpx.Request the tests have no reason to build."""

    def __init__(self, message):
        Exception.__init__(self, message)
        self.message = message


def _fake_success_call(client, model, system_prompt, user_message):
    return _FakeMessage(
        content=[
            _FakeToolUseBlock(
                name="propose_fix",
                input={
                    "explanation": "The dmStatus element was out of order.",
                    "corrected_xml": "<dmStatus>...</dmStatus>",
                    "confidence": "high",
                },
            )
        ]
    )


def test_missing_file_returns_structured_error_without_calling_api(monkeypatch):
    called = False

    def _should_not_be_called(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(server_module, "_get_anthropic_client", _should_not_be_called)

    result = suggest_fix("samples/corpus/DOES-NOT-EXIST.XML", SAMPLE_ERROR)

    assert result["ok"] is False
    assert "File not found" in result["reason"]
    assert called is False


def test_missing_api_key_returns_structured_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = suggest_fix(VALID_DM, SAMPLE_ERROR)

    assert result["ok"] is False
    assert "ANTHROPIC_API_KEY" in result["reason"]


def test_successful_call_returns_parsed_suggestion(monkeypatch):
    monkeypatch.setattr(server_module, "_get_anthropic_client", lambda: object())
    monkeypatch.setattr(server_module, "_call_propose_fix_api", _fake_success_call)

    result = suggest_fix(BAD_ENUM_DM, SAMPLE_ERROR)

    assert result["ok"] is True
    assert result["file"] == BAD_ENUM_DM
    assert result["model"] == DEFAULT_MODEL
    assert result["confidence"] == "high"
    assert "dmStatus" in result["explanation"]
    assert result["corrected_xml"] == "<dmStatus>...</dmStatus>"


def test_explicit_model_argument_overrides_default(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.setattr(server_module, "_get_anthropic_client", lambda: object())
    monkeypatch.setattr(server_module, "_call_propose_fix_api", _fake_success_call)

    result = suggest_fix(VALID_DM, SAMPLE_ERROR, model="claude-sonnet-4-5")

    assert result["model"] == "claude-sonnet-4-5"


def test_env_model_used_when_argument_omitted(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-4-1")
    monkeypatch.setattr(server_module, "_get_anthropic_client", lambda: object())
    monkeypatch.setattr(server_module, "_call_propose_fix_api", _fake_success_call)

    result = suggest_fix(VALID_DM, SAMPLE_ERROR)

    assert result["model"] == "claude-opus-4-1"


def test_api_error_returns_structured_error_not_raise(monkeypatch):
    def _raise_api_error(client, model, system_prompt, user_message):
        raise _FakeAPIError("rate limited")

    monkeypatch.setattr(server_module, "_get_anthropic_client", lambda: object())
    monkeypatch.setattr(server_module, "_call_propose_fix_api", _raise_api_error)

    result = suggest_fix(VALID_DM, SAMPLE_ERROR)

    assert result["ok"] is False
    # The raw API error detail ("rate limited") is logged server-side, not
    # returned to the caller; the caller gets a generic message.
    assert "rate limited" not in result["reason"]
    assert "fix service" in result["reason"]


def test_malformed_response_returns_structured_error_not_raise(monkeypatch):
    def _no_tool_use_call(client, model, system_prompt, user_message):
        return _FakeMessage(content=[])

    monkeypatch.setattr(server_module, "_get_anthropic_client", lambda: object())
    monkeypatch.setattr(server_module, "_call_propose_fix_api", _no_tool_use_call)

    result = suggest_fix(VALID_DM, SAMPLE_ERROR)

    assert result["ok"] is False
    assert "propose_fix" in result["reason"]


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; skipping live Anthropic API call",
)
def test_live_suggest_fix_against_real_api():
    """Real, unmocked round trip against the Anthropic API. Only runs when
    a real key is present (e.g. a developer's local .env), never in CI."""
    result = suggest_fix(BAD_ENUM_DM, SAMPLE_ERROR)

    assert result["ok"] is True
    assert result["corrected_xml"]
    assert result["explanation"]
    assert result["confidence"] in {"high", "medium", "low"}
