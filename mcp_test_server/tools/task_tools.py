"""Task tools and the MCP Tasks extension (`io.modelcontextprotocol/tasks`, protocol 2026-07-28).

A client that declares the Tasks extension in `_meta.io.modelcontextprotocol/clientCapabilities`
gets a `CreateTaskResult` from `task_run` and `task_scripted`, and then polls the task with
`tasks/get`. Every other client (and the REST API) runs the same tools synchronously. All other
tools always answer at once, which is also a valid answer to a Tasks client.

Each task is scripted by its arguments, so a test controls every step: `tasks/get` reports
`working` for `working_polls` checks, then the final outcome. Task IDs are `task-1`, `task-2`, ...
in creation order. The task store is the only state in the server. Each task stays readable for
its `ttlMs` after it is created, then it is removed and its ID is unknown (`-32602`). The store holds
up to `max_tasks` tasks; when it is full, a new task is refused with an error. A result over
`max_result_bytes` is not kept: the task fails with an error that names the limit. The server sets
all three (`--task-ttl-ms`, `--max-tasks`, `--max-task-result-bytes`).

Expiry uses `time.monotonic()`, which never appears in any response: `createdAt` and
`lastUpdatedAt` come from a logical clock, so responses stay deterministic.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.extension import Extension, MethodBinding
from mcp.server.mcpserver import require_client_extension
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, CallToolResult, RequestParams, TextContent
from pydantic import BaseModel, ValidationError

TASKS_EXTENSION = "io.modelcontextprotocol/tasks"
# The first protocol version with the flat Tasks extension. Versions are dates, so they compare
# as strings.
TASKS_PROTOCOL_VERSION = "2026-07-28"
TASK_TOOLS = frozenset({"task_run", "task_scripted"})

MIB = 1024 * 1024
# Defaults of the server options --task-ttl-ms, --max-tasks and --max-task-result-bytes.
DEFAULT_TASK_TTL_MS = 3_600_000
DEFAULT_MAX_TASKS = 1000
# 10 MiB is far more than a test result needs, and keeps the tasks/get response small.
DEFAULT_MAX_RESULT_BYTES = 10 * MIB
MAX_WORKING_POLLS = 100
MAX_POLL_INTERVAL_MS = 60_000
DEFAULT_WORKING_POLLS = 2
DEFAULT_POLL_INTERVAL_MS = 1000

# Start of the logical clock for createdAt / lastUpdatedAt, which SEP-2663 requires as ISO-8601.
CLOCK_START = datetime(2026, 1, 1, tzinfo=timezone.utc)

INTERNAL_ERROR = -32603
CANCELLED_BY_CLIENT = "Cancelled by client"
INPUT_REQUEST_KEY = "confirm"

# Outcomes that end a task. A task in any other status (working, input_required, or a status the
# extension does not define) can still be cancelled.
_TERMINAL = frozenset({"completed", "failed", "cancelled"})


# ---------------------------------------------------------------------------
# Argument checks, shared by the synchronous tools and the extension
# ---------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _options_error(working_polls: Any, poll_interval_ms: Any) -> str | None:
    if not _is_int(working_polls) or not 0 <= working_polls <= MAX_WORKING_POLLS:
        return f"working_polls must be an integer from 0 to {MAX_WORKING_POLLS}"
    if not _is_int(poll_interval_ms) or not 0 <= poll_interval_ms <= MAX_POLL_INTERVAL_MS:
        return f"poll_interval_ms must be an integer from 0 to {MAX_POLL_INTERVAL_MS}"
    return None


def _run_error(args: dict[str, Any]) -> str | None:
    tool = args.get("tool")
    if not isinstance(tool, str) or not tool:
        return "tool must be a non-empty string"
    if tool in TASK_TOOLS:
        return f"task_run cannot run the task tool '{tool}'"
    arguments = args.get("arguments")
    if arguments is not None and not isinstance(arguments, dict):
        return "arguments must be an object"
    return _options_error(
        args.get("working_polls", DEFAULT_WORKING_POLLS), args.get("poll_interval_ms", DEFAULT_POLL_INTERVAL_MS)
    )


def _scripted_error(args: dict[str, Any]) -> str | None:
    outcome = args.get("outcome", "completed")
    if not isinstance(outcome, str) or not outcome:
        return "outcome must be a non-empty string"
    if not isinstance(args.get("message", ""), str):
        return "message must be a string"
    return _options_error(
        args.get("working_polls", DEFAULT_WORKING_POLLS), args.get("poll_interval_ms", DEFAULT_POLL_INTERVAL_MS)
    )


class _RunArgs(BaseModel):
    """task_run's arguments, parsed with the same pydantic rules as the SDK (so "2" is 2)."""

    tool: str
    arguments: dict[str, Any] | None = None
    working_polls: int = DEFAULT_WORKING_POLLS
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS


class _ScriptedArgs(BaseModel):
    """task_scripted's arguments, parsed with the same pydantic rules as the SDK."""

    outcome: str = "completed"
    message: str = ""
    working_polls: int = DEFAULT_WORKING_POLLS
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS


def _pre_parse_json(model: type[BaseModel], args: dict[str, Any]) -> dict[str, Any]:
    """Decode JSON strings sent for non-string fields, as the SDK does before validation.

    Copy of `FuncMetadata.pre_parse_json` in `mcp.server.mcpserver.utilities.func_metadata`
    (mcp 2.3.0). Some clients send an object as a JSON string, for example
    `"arguments": "{\\"a\\": 2}"`. A decoded object, array, bool or null replaces the string; a
    decoded string or number does not. Keep this in step with the SDK.
    """
    parsed = dict(args)
    for key, value in args.items():
        field = model.model_fields.get(key)
        if field is None or field.annotation is str or not isinstance(value, str):
            continue
        try:
            decoded = json.loads(value)
        except (ValueError, RecursionError):
            continue
        if not isinstance(decoded, str | int | float):
            parsed[key] = decoded
    return parsed


def _task_args(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """The parsed arguments of a task tool call, or None if they are invalid.

    Parsing follows the SDK's own argument handling (JSON pre-parsing, then pydantic validation),
    so a call that the tool accepts runs as a task, and a call that the tool rejects gets the
    tool's error at once.
    """
    model, check = (_RunArgs, _run_error) if name == "task_run" else (_ScriptedArgs, _scripted_error)
    try:
        parsed = model.model_validate(_pre_parse_json(model, args)).model_dump()
    except ValidationError:
        return None
    return None if check(parsed) else parsed


def _input_requests(message: str) -> dict[str, Any]:
    """The fixed `inputRequests` map of an input_required task (SEP-2322 requires a non-empty map).

    The task never uses the answer: it stays input_required until the client cancels it.
    """
    return {
        INPUT_REQUEST_KEY: {
            "method": "elicitation/create",
            "params": {
                "message": message or "mcp-testkit: confirm to continue",
                "requestedSchema": {
                    "type": "object",
                    "properties": {"confirmed": {"type": "boolean", "description": "Confirm?"}},
                },
            },
        }
    }


def _error_result(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": message}))])


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def register(mcp):
    @mcp.tool()
    async def task_run(
        tool: str,
        arguments: dict[str, Any] | None = None,
        working_polls: int = DEFAULT_WORKING_POLLS,
        poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
    ) -> CallToolResult:
        """Run another tool as an MCP task: working for working_polls checks, then completed with that tool's result."""
        error = _run_error(
            {"tool": tool, "arguments": arguments, "working_polls": working_polls, "poll_interval_ms": poll_interval_ms}
        )
        if error:
            return _error_result(error)
        try:
            return await mcp.call_tool(tool, arguments or {})
        except ToolError as e:
            # Report the inner tool's error as it is. Raising it again would make the SDK add a
            # second "Error executing tool task_run: " prefix.
            return CallToolResult(content=[TextContent(type="text", text=str(e))], is_error=True)

    @mcp.tool()
    def task_scripted(
        outcome: str = "completed",
        message: str = "",
        working_polls: int = DEFAULT_WORKING_POLLS,
        poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
    ) -> str:
        """Run a scripted MCP task: working for working_polls checks, then the outcome (completed, failed, cancelled, tool_error, input_required, or any other status)."""
        error = _scripted_error(
            {
                "outcome": outcome,
                "message": message,
                "working_polls": working_polls,
                "poll_interval_ms": poll_interval_ms,
            }
        )
        if error:
            return json.dumps({"error": error})
        if outcome == "completed":
            return json.dumps({"result": message})
        if outcome == "tool_error":
            raise ToolError(message or "Tool error")
        if outcome == "failed":
            raise ToolError(message or "Task failed")
        raise ToolError(f"Outcome '{outcome}' needs a client that declares the {TASKS_EXTENSION} extension")


# ---------------------------------------------------------------------------
# Tasks extension
# ---------------------------------------------------------------------------


class TaskIdParams(RequestParams):
    task_id: str


def _timestamp(seconds: int) -> str:
    """An ISO-8601 time on the logical clock: CLOCK_START plus `seconds`. Never the current time."""
    return (CLOCK_START + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class _Task:
    task_id: str
    number: int
    working_polls: int
    poll_interval_ms: int
    outcome: dict[str, Any]
    ttl_ms: int
    expires_at: float  # on the monotonic clock; used only to remove the task
    polls: int = 0

    def is_finished(self) -> bool:
        """True once a tasks/get has reported a terminal outcome."""
        return self.polls > self.working_polls and self.outcome["status"] in _TERMINAL

    def view(self, fields: dict[str, Any]) -> dict[str, Any]:
        # Logical clock: task N is created at second N. Each tasks/get advances lastUpdatedAt one
        # second until the check that reports the final outcome; after that it no longer changes.
        updates = min(self.polls, self.working_polls + 1)
        task = {
            "taskId": self.task_id,
            "createdAt": _timestamp(self.number),
            "lastUpdatedAt": _timestamp(self.number + updates),
            "ttlMs": self.ttl_ms,
            **fields,
        }
        if task["status"] not in _TERMINAL and self.poll_interval_ms > 0:
            task["pollIntervalMs"] = self.poll_interval_ms
        return task

    def poll(self) -> dict[str, Any]:
        self.polls += 1
        if self.polls <= self.working_polls:
            return self.view({"status": "working"})
        return self.view(self.outcome)


def _declares_tasks(ctx) -> bool:
    """True if this request may get a task: protocol 2026-07-28 or later, with the extension declared.

    An older session cannot carry a flat `resultType:"task"` result, and the server does not
    advertise the extension to it, so it always gets the synchronous result, even if it declared
    the extension in `initialize`.
    """
    if not ctx.protocol_version or ctx.protocol_version < TASKS_PROTOCOL_VERSION:
        return False
    capabilities = ctx.session.client_capabilities
    return bool(capabilities and capabilities.extensions and TASKS_EXTENSION in capabilities.extensions)


def _tool_result(result: Any) -> dict[str, Any]:
    """The `CallToolResult` to embed in a completed task, without the envelope fields."""
    data = result.model_dump(by_alias=True, mode="json", exclude_none=True) if hasattr(result, "model_dump") else result
    data = dict(data)
    data.pop("resultType", None)
    data.pop("_meta", None)
    return data


class TasksExtension(Extension):
    """Serves `task_run` and `task_scripted` as tasks, plus `tasks/get`, `tasks/cancel` and `tasks/update`."""

    identifier = TASKS_EXTENSION

    def __init__(
        self,
        max_tasks: int = DEFAULT_MAX_TASKS,
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
        ttl_ms: int = DEFAULT_TASK_TTL_MS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._tasks: dict[str, _Task] = {}
        self._next_id = 1
        self._clock = clock
        self.configure(max_tasks=max_tasks, max_result_bytes=max_result_bytes, ttl_ms=ttl_ms)

    def configure(self, *, max_tasks: int, max_result_bytes: int, ttl_ms: int) -> None:
        """Sets the limits for tasks created from now on (the server calls it with its options)."""
        self._max_tasks = max_tasks
        self._max_result_bytes = max_result_bytes
        self._ttl_ms = ttl_ms

    def methods(self):
        return [
            MethodBinding("tasks/get", TaskIdParams, self._tasks_get),
            MethodBinding("tasks/cancel", TaskIdParams, self._tasks_cancel),
            MethodBinding("tasks/update", TaskIdParams, self._tasks_update),
        ]

    async def intercept_tool_call(self, params, ctx, call_next):
        if params.name not in TASK_TOOLS or not _declares_tasks(ctx):
            return await call_next(ctx)
        args = _task_args(params.name, params.arguments or {})
        if args is None:
            # Invalid arguments: the tool itself answers at once with the error.
            return await call_next(ctx)
        outcome = self._within_result_limit(await self._outcome(params.name, args, ctx, call_next))
        task = self._create(args["working_polls"], args["poll_interval_ms"], outcome)
        return {"resultType": "task", **task.view({"status": "working"})}

    async def _outcome(self, name, args, ctx, call_next) -> dict[str, Any]:
        """The final task fields. The tool runs now; tasks/get reveals the outcome later."""
        if name == "task_run":
            return {"status": "completed", "result": _tool_result(await call_next(ctx))}
        outcome = args["outcome"]
        message = args["message"]
        if outcome in ("completed", "tool_error"):
            # The tool gives the result: {"result": message}, or isError with the message.
            return {"status": "completed", "result": _tool_result(await call_next(ctx))}
        if outcome == "failed":
            return {"status": "failed", "error": {"code": INTERNAL_ERROR, "message": message or "Task failed"}}
        if outcome == "cancelled":
            return {"status": "cancelled", "statusMessage": message or "Cancelled by server"}
        if outcome == "input_required":
            return {"status": "input_required", "inputRequests": _input_requests(message)}
        return {"status": outcome, **({"statusMessage": message} if message else {})}

    def _within_result_limit(self, outcome: dict[str, Any]) -> dict[str, Any]:
        """The outcome, or a `failed` outcome that explains the limit if it is too large to keep."""
        size = len(json.dumps(outcome).encode())
        if size <= self._max_result_bytes:
            return outcome
        return {
            "status": "failed",
            "error": {
                "code": INTERNAL_ERROR,
                "message": (
                    f"Task result is {size:,} bytes as JSON, over the limit of {self._max_result_bytes:,} bytes "
                    "that mcp-testkit keeps for one task (--max-task-result-bytes). "
                    "Use a smaller result, for example a smaller echo_large size_kb."
                ),
            },
        }

    def _create(self, working_polls: int, poll_interval_ms: int, outcome: dict[str, Any]) -> _Task:
        self._remove_expired()
        if len(self._tasks) >= self._max_tasks:
            raise MCPError(
                code=INTERNAL_ERROR,
                message=(
                    f"mcp-testkit task store is full: {len(self._tasks):,} of {self._max_tasks:,} tasks (--max-tasks). "
                    f"A task is removed {self._ttl_ms:,} ms after it is created (--task-ttl-ms). "
                    "Retry later, or start the server with a higher --max-tasks."
                ),
            )
        task = _Task(
            f"task-{self._next_id}",
            self._next_id,
            working_polls,
            poll_interval_ms,
            outcome,
            ttl_ms=self._ttl_ms,
            expires_at=self._clock() + self._ttl_ms / 1000,
        )
        self._next_id += 1
        self._tasks[task.task_id] = task
        return task

    def _remove_expired(self) -> None:
        """Removes every task whose ttlMs has passed since it was created."""
        now = self._clock()
        for task_id in [t.task_id for t in self._tasks.values() if t.expires_at <= now]:
            del self._tasks[task_id]

    def _lookup(self, ctx, task_id: str) -> _Task:
        require_client_extension(ctx, TASKS_EXTENSION)
        self._remove_expired()
        task = self._tasks.get(task_id)
        if task is None:
            raise MCPError(code=INVALID_PARAMS, message=f"Unknown task: {task_id}")
        return task

    async def _tasks_get(self, ctx, params: TaskIdParams):
        return {"resultType": "complete", **self._lookup(ctx, params.task_id).poll()}

    async def _tasks_cancel(self, ctx, params: TaskIdParams):
        """Cancels a task that has not finished; a finished task is left as it is. Both get the ack."""
        task = self._lookup(ctx, params.task_id)
        if not task.is_finished():
            task.outcome = {"status": "cancelled", "statusMessage": CANCELLED_BY_CLIENT}
            task.working_polls = task.polls
        return {"resultType": "complete"}

    async def _tasks_update(self, ctx, params: TaskIdParams):
        """Acknowledges input for a task with the empty acknowledgement.

        The input is ignored: an input_required task does not resume, and stays input_required until
        the client cancels it. The task must exist, and its status does not change.
        """
        self._lookup(ctx, params.task_id)
        return {"resultType": "complete"}
