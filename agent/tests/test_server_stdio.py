"""End to end: launch the server as a subprocess and talk MCP over stdio, as Claude does."""

import asyncio
import sys

from mcp import Client, StdioServerParameters

from semantic import PROJECT_DIR


def _session(check):
    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(PROJECT_DIR / "agent" / "server.py")])
        async with Client(params) as client:
            return await check(client)

    return asyncio.run(run())


def test_tools_are_listed_and_read_only(layer):
    async def check(client):
        return (await client.list_tools()).tools

    tools = {t.name: t for t in _session(check)}
    assert set(tools) == {"list_metrics", "query_metric"}
    assert all(t.annotations.read_only_hint for t in tools.values())


def test_query_and_error_over_stdio(layer):
    async def check(client):
        ok = await client.call_tool("query_metric", {"metrics": ["mrr"], "grain": "year"})
        bad = await client.call_tool("query_metric", {"metrics": ["revenue"]})
        return ok, bad

    ok, bad = _session(check)
    assert not ok.is_error and len(ok.structured_content["rows"]) >= 2
    assert bad.is_error and "Suggestions" in bad.content[0].text
