"""S1000D MCP server.

Week 1 milestone: a minimal, working MCP server with a single "hello world"
tool, confirming the server starts and a tool call round-trips correctly
before the real S1000D review tools (schema validation, cross-reference
checking, applicability, suggest_fix) are layered in over the following
weeks.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    name="s1000d-mcp",
    version="0.1.0",
    instructions=(
        "Tools for reviewing S1000D data modules: schema validation, "
        "cross-reference checking, applicability checking, and "
        "LLM-assisted fix suggestions. Currently in early development."
    ),
)


@mcp.tool()
def ping(message: str = "hello from s1000d-mcp") -> str:
    """Health-check tool: echoes a message back with a server tag.

    Used to confirm the server is reachable and a tool call round-trips
    correctly, before real S1000D tooling is added.
    """
    return f"pong: {message}"


def main() -> None:
    """Entry point for the `s1000d-mcp` console script (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
