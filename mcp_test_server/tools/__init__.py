"""Tool group registry for MCP test server."""

from mcp_test_server.tools import (
    collection_tools,
    conversion_tools,
    datetime_tools,
    echo_tools,
    encoding_tools,
    math_tools,
    string_tools,
    validation_tools,
)

ALL_GROUPS = [
    math_tools,
    string_tools,
    collection_tools,
    encoding_tools,
    datetime_tools,
    validation_tools,
    conversion_tools,
    echo_tools,
]


def register_all(mcp):
    """Register all tool groups with the MCP server."""
    for group in ALL_GROUPS:
        group.register(mcp)
