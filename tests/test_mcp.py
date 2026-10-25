"""The same promise, one layer up: through the MCP tool interface that a model actually calls."""
from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from conftest import HIDDEN_IDS, MARKERS
from hushbrain.server import build_server

pytestmark = pytest.mark.db

EXPECTED_TOOLS = {
    "search", "cross_project", "recent", "project_context", "what_changed_since", "get_body", "stats",
}
CALLS = {
    "search": {"query": "family weekend knee physio salary fence cottage", "limit": 20},
    "cross_project": {"query": "private legal health family", "limit": 20},
    "recent": {"limit": 20},
    "project_context": {"project_id": "orchard-lane-dispute"},
    "what_changed_since": {"since_iso": "2000-01-01T00:00:00+00:00"},
    "get_body": {"id": "decision-boundary-fence-settlement"},
    "stats": {},
}


@pytest.fixture(scope="module")
def mcp(loaded):
    return build_server(loaded)


def call(mcp, name, args):
    async def go():
        content, structured = await mcp.call_tool(name, args)
        return structured if structured is not None else json.loads(content[0].text)
    return asyncio.run(go())


def test_the_server_exposes_exactly_the_documented_tools(mcp):
    tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS


def test_every_tool_defaults_include_sensitive_to_false(mcp):
    """A tool added later without the flag, or with a permissive default, fails here."""
    for t in asyncio.run(mcp.list_tools()):
        prop = t.inputSchema["properties"].get("include_sensitive")
        assert prop is not None, f"{t.name} has no include_sensitive parameter"
        assert prop["default"] is False, f"{t.name} defaults include_sensitive to {prop['default']!r}"
        assert "include_sensitive" not in t.inputSchema.get("required", [])


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_default_call_through_mcp_leaks_nothing(mcp, name):
    text = json.dumps(call(mcp, name, CALLS[name]), default=str).lower()
    for hid in HIDDEN_IDS - {"decision-boundary-fence-settlement"} if name == "get_body" else HIDDEN_IDS:
        assert hid not in text
    for marker in MARKERS:
        assert marker not in text


def test_get_body_through_mcp_is_refused_then_allowed(mcp):
    refused = call(mcp, "get_body", CALLS["get_body"])
    assert refused["error"] == "sensitive node withheld"
    assert refused["sensitivity"] == "legal"
    allowed = call(mcp, "get_body", {**CALLS["get_body"], "include_sensitive": True})
    assert "canary-legal-fence" in allowed["body"]


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
@pytest.mark.parametrize("not_a_boolean", ["true", "True", "false", "yes", "1", 1, 0, None])
def test_only_a_json_boolean_is_accepted_for_the_flag(mcp, name, not_a_boolean):
    """pydantic's lax mode would read "true", "yes", "1" and 1 as True, which would let a model
    opt in with a value a host-side hook matching on `include_sensitive: true` never sees.
    The flag is strict, so these are errors and nothing is read."""
    with pytest.raises(ToolError):
        call(mcp, name, {**CALLS[name], "include_sensitive": not_a_boolean})


def test_a_real_false_is_accepted_and_still_hides_the_notes(mcp):
    r = call(mcp, "recent", {"limit": 20, "include_sensitive": False})
    assert not ({x["id"] for x in r["results"]} & HIDDEN_IDS)
