"""End to end: start the real `hushbrain serve` process and talk MCP to it over stdio."""
from __future__ import annotations

import asyncio
import json
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conftest import EXAMPLE_POLICY, HIDDEN_IDS, MARKERS

pytestmark = pytest.mark.db


async def _session_calls(env):
    params = StdioServerParameters(command=sys.executable, args=["-m", "hushbrain", "serve"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            rec = await session.call_tool("recent", {"limit": 20})
            body = await session.call_tool("get_body", {"id": "note-physio-exercises"})
            body_ok = await session.call_tool(
                "get_body", {"id": "note-physio-exercises", "include_sensitive": True})
            return (tools, json.loads(rec.content[0].text),
                    json.loads(body.content[0].text), json.loads(body_ok.content[0].text))


def test_serve_over_stdio_hides_private_notes_by_default(loaded):
    env = {
        "HUSHBRAIN_DSN": loaded.dsn,
        "HUSHBRAIN_EMBEDDER": "hash",
        "HUSHBRAIN_EMBED_DIM": "64",
        "HUSHBRAIN_POLICY_PATH": str(EXAMPLE_POLICY),
    }
    tools, recent, refused, allowed = asyncio.run(asyncio.wait_for(_session_calls(env), 60))
    assert "get_body" in tools and "search" in tools
    text = json.dumps(recent).lower()
    assert not any(h in text for h in HIDDEN_IDS) and not any(m in text for m in MARKERS)
    assert refused["error"] == "sensitive node withheld"
    assert "canary-personal-physio" in allowed["body"]
