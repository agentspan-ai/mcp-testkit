"""MCP Test Server — 64 deterministic tools for MCP protocol testing."""

import argparse

from mcp.server.fastmcp import FastMCP

from tools import register_all

mcp = FastMCP(
    name="mcp-test-server",
    instructions="A test server with 64 deterministic tools across 8 groups for MCP protocol testing.",
)

register_all(mcp)


def main():
    parser = argparse.ArgumentParser(description="MCP Test Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="SSE host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="SSE port (default: 8000)")
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
