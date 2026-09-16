"""End-to-end smoke test: spawn the real MCP server over stdio and call
its `ping` tool, the same way a client (Claude Desktop/Code) would.
"""

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_ping_tool_round_trips():
    params = StdioServerParameters(
        command="uv",
        args=["run", "s1000d-mcp"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            assert "ping" in names

            result = await session.call_tool("ping", {"message": "week 1"})
            text = result.content[0].text
            assert text == "pong: week 1"
