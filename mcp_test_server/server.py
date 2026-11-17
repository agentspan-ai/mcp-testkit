"""MCP Test Server — deterministic tools for MCP protocol testing."""

import argparse
import asyncio

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_test_server import __version__
from mcp_test_server.api import create_api_routes
from mcp_test_server.tools import register_all
from mcp_test_server.tools.task_tools import (
    DEFAULT_MAX_RESULT_BYTES,
    DEFAULT_MAX_TASKS,
    DEFAULT_TASK_TTL_MS,
    TasksExtension,
)

tasks_extension = TasksExtension()

mcp = MCPServer(
    name="mcp-testkit",
    version=__version__,
    instructions="A test server with deterministic tools for MCP protocol testing.",
    extensions=[tasks_extension],
)

register_all(mcp)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that requires a Bearer token on every request."""

    def __init__(self, app, auth_key: str):
        super().__init__(app)
        self.auth_key = auth_key

    async def dispatch(self, request: Request, call_next):
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer ") or auth_header[7:] != self.auth_key:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)


def _build_http_app(auth_key=None, host="127.0.0.1", max_request_body_size=DEFAULT_MAX_REQUEST_BODY_SIZE):
    """Build Streamable HTTP Starlette app with REST API routes and optional auth.

    `max_request_body_size` bounds `/mcp` request bodies (413 above it). The SDK default, 4 MiB, is
    a security fix (GHSA-fmmv-w9g8-j3gc): without a limit, one large request can exhaust memory.
    """
    app = mcp.streamable_http_app(stateless_http=True, host=host, max_request_body_size=max_request_body_size)

    # Add REST API routes
    api_routes = create_api_routes(mcp)
    for route in api_routes:
        app.routes.append(route)

    if auth_key:
        app.add_middleware(BearerAuthMiddleware, auth_key=auth_key)

    return app


def _positive_int(value):
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value!r}")
    return number


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="MCP Test Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=3001, help="HTTP port (default: 3001)")
    parser.add_argument("--auth", default=None, help="Bearer auth key (enables authentication for HTTP)")
    parser.add_argument(
        "--max-request-body-size",
        type=_positive_int,
        default=DEFAULT_MAX_REQUEST_BODY_SIZE,
        metavar="BYTES",
        help=(
            "Largest /mcp request body in bytes; larger requests get HTTP 413 "
            f"(default: {DEFAULT_MAX_REQUEST_BODY_SIZE}, 4 MiB, the MCP SDK default)"
        ),
    )
    parser.add_argument(
        "--max-tasks",
        type=_positive_int,
        default=DEFAULT_MAX_TASKS,
        metavar="N",
        help=f"Most MCP tasks kept at once; when full, a new task is refused (default: {DEFAULT_MAX_TASKS})",
    )
    parser.add_argument(
        "--max-task-result-bytes",
        type=_positive_int,
        default=DEFAULT_MAX_RESULT_BYTES,
        metavar="BYTES",
        help=(
            "Largest result one MCP task keeps, as JSON; a larger result makes the task fail "
            f"(default: {DEFAULT_MAX_RESULT_BYTES}, 10 MiB)"
        ),
    )
    parser.add_argument(
        "--task-ttl-ms",
        type=_positive_int,
        default=DEFAULT_TASK_TTL_MS,
        metavar="MS",
        help=(
            "How long an MCP task stays readable after it is created; also the ttlMs sent to clients "
            f"(default: {DEFAULT_TASK_TTL_MS}, 1 hour)"
        ),
    )
    return parser.parse_args(argv)


def main():
    args = _parse_args()
    tasks_extension.configure(
        max_tasks=args.max_tasks, max_result_bytes=args.max_task_result_bytes, ttl_ms=args.task_ttl_ms
    )

    if args.transport == "http":
        app = _build_http_app(auth_key=args.auth, host=args.host, max_request_body_size=args.max_request_body_size)
        config = uvicorn.Config(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        asyncio.run(server.serve())
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
