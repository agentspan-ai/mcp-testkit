"""Tests for the task tools and the MCP Tasks extension (protocol 2026-07-28)."""

import asyncio
import json
import selectors
import subprocess
import sys
import unittest
from datetime import datetime

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from starlette.testclient import TestClient

from mcp_test_server import __version__
from mcp_test_server.tools import register_all
from mcp_test_server.tools.task_tools import (
    DEFAULT_MAX_RESULT_BYTES,
    DEFAULT_MAX_TASKS,
    DEFAULT_TASK_TTL_MS,
    TASKS_EXTENSION,
    TaskIdParams,
    TasksExtension,
)

PROTOCOL_VERSION = "2026-07-28"


def _make_server(extension=None):
    mcp = MCPServer(name="test", extensions=[extension or TasksExtension()])
    register_all(mcp)
    return mcp


def _call(mcp, name, args):
    result = asyncio.run(mcp.call_tool(name, args))
    return json.loads(result.content[0].text)


def _meta(tasks):
    capabilities = {"extensions": {TASKS_EXTENSION: {}}} if tasks else {}
    return {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientInfo": {"name": "test-client", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": capabilities,
    }


class _HttpCase(unittest.TestCase):
    """Sends stateless 2026-07-28 JSON-RPC requests to the streamable HTTP app."""

    def make_extension(self):
        return TasksExtension()

    def setUp(self):
        self.mcp = _make_server(self.make_extension())
        app = self.mcp.streamable_http_app(stateless_http=True, json_response=True)
        self.client = self.enterContext(TestClient(app, base_url="http://127.0.0.1:3001"))

    def rpc(self, method, params, tasks=True, name=None):
        params = {**params, "_meta": _meta(tasks)}
        headers = {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Mcp-Method": method,
        }
        if name is not None:
            headers["Mcp-Name"] = name
        r = self.client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers=headers
        )
        return r.json()

    def call(self, tool, arguments, tasks=True):
        response = self.rpc("tools/call", {"name": tool, "arguments": arguments}, tasks=tasks, name=tool)
        self.assertIn("result", response, response)
        return response["result"]

    def get(self, task_id, tasks=True):
        return self.rpc("tasks/get", {"taskId": task_id}, tasks=tasks, name=task_id)

    def cancel(self, task_id):
        return self.rpc("tasks/cancel", {"taskId": task_id}, name=task_id)

    def get_result(self, task_id):
        response = self.get(task_id)
        self.assertIn("result", response, response)
        return response["result"]


# ---------- synchronous behaviour (no Tasks extension, REST) ----------


class TestTaskToolsSync(unittest.TestCase):
    def setUp(self):
        self.mcp = _make_server()

    def test_run_returns_the_tool_result(self):
        self.assertEqual(
            _call(self.mcp, "task_run", {"tool": "math_add", "arguments": {"a": 2, "b": 3}}), {"result": 5.0}
        )

    def test_run_without_arguments(self):
        self.assertEqual(_call(self.mcp, "task_run", {"tool": "echo_types"})["string"], "hello")

    def test_run_rejects_task_tools(self):
        r = _call(self.mcp, "task_run", {"tool": "task_scripted"})
        self.assertIn("cannot run the task tool", r["error"])

    def test_run_rejects_out_of_range_polls(self):
        r = _call(self.mcp, "task_run", {"tool": "echo", "arguments": {"message": "x"}, "working_polls": 101})
        self.assertIn("working_polls", r["error"])

    def test_run_unknown_tool_is_a_tool_error(self):
        r = asyncio.run(self.mcp.call_tool("task_run", {"tool": "no_such_tool"}))
        self.assertTrue(r.is_error)
        self.assertIn("no_such_tool", r.content[0].text)

    def test_run_nested_tool_error_has_one_prefix(self):
        r = asyncio.run(self.mcp.call_tool("task_run", {"tool": "echo_error", "arguments": {"message": "x"}}))
        self.assertTrue(r.is_error)
        self.assertEqual(r.content[0].text, "Error executing tool echo_error: Intentional error: x")

    def test_scripted_completed(self):
        self.assertEqual(
            _call(self.mcp, "task_scripted", {"outcome": "completed", "message": "done"}), {"result": "done"}
        )

    def test_scripted_defaults_to_completed(self):
        self.assertEqual(_call(self.mcp, "task_scripted", {}), {"result": ""})

    def test_scripted_failed_raises(self):
        with self.assertRaises(ToolError) as ctx:
            asyncio.run(self.mcp.call_tool("task_scripted", {"outcome": "failed", "message": "boom"}))
        self.assertIn("boom", str(ctx.exception))

    def test_scripted_needs_tasks_for_other_outcomes(self):
        with self.assertRaises(ToolError) as ctx:
            asyncio.run(self.mcp.call_tool("task_scripted", {"outcome": "input_required"}))
        self.assertIn(TASKS_EXTENSION, str(ctx.exception))

    def test_scripted_rejects_negative_interval(self):
        r = _call(self.mcp, "task_scripted", {"poll_interval_ms": -1})
        self.assertIn("poll_interval_ms", r["error"])


# ---------- Tasks extension over HTTP ----------


class TestTaskCreation(_HttpCase):
    def test_create_task_result(self):
        r = self.call("task_run", {"tool": "math_add", "arguments": {"a": 2, "b": 3}})
        self.assertEqual(r["resultType"], "task")
        self.assertEqual(r["taskId"], "task-1")
        self.assertEqual(r["status"], "working")
        self.assertEqual(r["pollIntervalMs"], 1000)
        self.assertEqual(r["ttlMs"], DEFAULT_TASK_TTL_MS)
        self.assertNotIn("result", r)

    def test_task_ids_are_sequential(self):
        ids = [self.call("task_scripted", {})["taskId"] for _ in range(3)]
        self.assertEqual(ids, ["task-1", "task-2", "task-3"])

    def test_custom_poll_interval(self):
        self.assertEqual(self.call("task_scripted", {"poll_interval_ms": 250})["pollIntervalMs"], 250)

    def test_zero_poll_interval_is_omitted(self):
        task_id = self.call("task_scripted", {"poll_interval_ms": 0})["taskId"]
        self.assertNotIn("pollIntervalMs", self.get_result(task_id))

    def test_client_without_tasks_gets_an_immediate_result(self):
        r = self.call("task_run", {"tool": "math_add", "arguments": {"a": 2, "b": 3}}, tasks=False)
        self.assertEqual(r["resultType"], "complete")
        self.assertEqual(json.loads(r["content"][0]["text"]), {"result": 5.0})

    def test_other_tools_answer_a_tasks_client_at_once(self):
        r = self.call("math_add", {"a": 2, "b": 3})
        self.assertEqual(r["resultType"], "complete")
        self.assertEqual(json.loads(r["content"][0]["text"]), {"result": 5.0})

    def test_invalid_arguments_answer_at_once(self):
        r = self.call("task_run", {"tool": "task_run"})
        self.assertEqual(r["resultType"], "complete")
        self.assertIn("error", json.loads(r["content"][0]["text"]))

    def test_create_task_result_has_iso_timestamps(self):
        r = self.call("task_scripted", {})
        self.assertEqual(r["createdAt"], "2026-01-01T00:00:01Z")
        self.assertEqual(r["lastUpdatedAt"], "2026-01-01T00:00:01Z")
        for key in ("createdAt", "lastUpdatedAt"):
            datetime.strptime(r[key], "%Y-%m-%dT%H:%M:%SZ")

    def test_each_task_gets_its_own_created_at(self):
        self.call("task_scripted", {})
        self.assertEqual(self.call("task_scripted", {})["createdAt"], "2026-01-01T00:00:02Z")

    def test_last_updated_at_advances_with_each_check(self):
        task_id = self.call("task_scripted", {"working_polls": 1})["taskId"]
        first, second = self.get_result(task_id), self.get_result(task_id)
        self.assertEqual(first["createdAt"], "2026-01-01T00:00:01Z")
        self.assertEqual(first["lastUpdatedAt"], "2026-01-01T00:00:02Z")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["createdAt"], "2026-01-01T00:00:01Z")
        self.assertEqual(second["lastUpdatedAt"], "2026-01-01T00:00:03Z")

    def test_last_updated_stops_at_the_final_status_for_any_outcome(self):
        task_id = self.call("task_scripted", {"outcome": "input_required", "working_polls": 0})["taskId"]
        times = [self.get_result(task_id)["lastUpdatedAt"] for _ in range(3)]
        self.assertEqual(times, ["2026-01-01T00:00:02Z"] * 3)

    def test_timestamps_are_deterministic_across_servers(self):
        other = _HttpCase()
        other.setUp()
        self.addCleanup(other.doCleanups)
        self.assertEqual(self.call("task_scripted", {}), other.call("task_scripted", {}))

    def test_numeric_strings_are_parsed_like_the_sdk(self):
        r = self.call("task_scripted", {"message": "done", "working_polls": "2", "poll_interval_ms": "500"})
        self.assertEqual(r["resultType"], "task")
        self.assertEqual(r["pollIntervalMs"], 500)
        task_id = r["taskId"]
        self.assertEqual([self.get_result(task_id)["status"] for _ in range(3)], ["working", "working", "completed"])

    def test_run_numeric_string_zero_polls(self):
        r = self.call("task_run", {"tool": "math_add", "arguments": {"a": 2, "b": 3}, "working_polls": "0"})
        self.assertEqual(r["resultType"], "task")
        self.assertEqual(self.get_result(r["taskId"])["status"], "completed")

    def test_run_arguments_as_json_string_creates_a_task(self):
        r = self.call("task_run", {"tool": "math_add", "arguments": '{"a": 2, "b": 3}', "working_polls": 0})
        self.assertEqual(r["resultType"], "task")
        result = self.get_result(r["taskId"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(json.loads(result["result"]["content"][0]["text"]), {"result": 5.0})

    def test_run_arguments_as_json_array_string_answers_at_once_with_the_tool_error(self):
        r = self.call("task_run", {"tool": "math_add", "arguments": "[1, 2]"})
        self.assertEqual(r["resultType"], "complete")
        self.assertTrue(r["isError"])

    def test_unparsable_number_answers_at_once_with_the_tool_error(self):
        r = self.call("task_scripted", {"working_polls": "abc"})
        self.assertEqual(r["resultType"], "complete")
        self.assertTrue(r["isError"])

    def test_server_advertises_the_extension(self):
        r = self.rpc("server/discover", {})
        self.assertEqual(r["result"]["capabilities"]["extensions"], {TASKS_EXTENSION: {}})


class TestTaskPolling(_HttpCase):
    def test_working_then_completed_with_the_tool_result(self):
        task_id = self.call("task_run", {"tool": "math_add", "arguments": {"a": 2, "b": 3}})["taskId"]
        for _ in range(2):
            r = self.get_result(task_id)
            self.assertEqual(r["status"], "working")
            self.assertEqual(r["pollIntervalMs"], 1000)
            self.assertNotIn("result", r)
        r = self.get_result(task_id)
        self.assertEqual(r["resultType"], "complete")
        self.assertEqual(r["taskId"], task_id)
        self.assertEqual(r["status"], "completed")
        self.assertNotIn("pollIntervalMs", r)
        self.assertFalse(r["result"]["isError"])
        self.assertEqual(json.loads(r["result"]["content"][0]["text"]), {"result": 5.0})
        self.assertNotIn("resultType", r["result"])

    def test_result_stays_after_completion(self):
        task_id = self.call("task_scripted", {"message": "done", "working_polls": 0})["taskId"]
        first = self.get_result(task_id)
        self.assertEqual(self.get_result(task_id), first)
        self.assertEqual(json.loads(first["result"]["content"][0]["text"]), {"result": "done"})

    def test_zero_working_polls_completes_on_first_check(self):
        task_id = self.call("task_run", {"tool": "echo", "arguments": {"message": "hi"}, "working_polls": 0})["taskId"]
        r = self.get_result(task_id)
        self.assertEqual(r["status"], "completed")
        self.assertEqual(json.loads(r["result"]["content"][0]["text"]), {"result": "hi"})

    def test_run_keeps_structured_content(self):
        task_id = self.call("task_run", {"tool": "echo", "arguments": {"message": "hi"}, "working_polls": 0})["taskId"]
        self.assertIn("structuredContent", self.get_result(task_id)["result"])

    def test_run_tool_error_completes_with_is_error(self):
        args = {"tool": "echo_error", "arguments": {"message": "bad"}, "working_polls": 0}
        r = self.get_result(self.call("task_run", args)["taskId"])
        self.assertEqual(r["status"], "completed")
        self.assertTrue(r["result"]["isError"])
        self.assertEqual(r["result"]["content"][0]["text"], "Error executing tool echo_error: Intentional error: bad")

    def test_run_unknown_tool_completes_with_is_error(self):
        r = self.get_result(self.call("task_run", {"tool": "no_such_tool", "working_polls": 0})["taskId"])
        self.assertEqual(r["status"], "completed")
        self.assertTrue(r["result"]["isError"])

    def test_run_multiple_content_blocks(self):
        args = {"tool": "echo_multiple", "arguments": {"messages": ["a", "b"]}, "working_polls": 0}
        r = self.get_result(self.call("task_run", args)["taskId"])
        self.assertEqual([c["text"] for c in r["result"]["content"]], ["a", "b"])

    def test_scripted_failed(self):
        task_id = self.call("task_scripted", {"outcome": "failed", "message": "remote failure", "working_polls": 1})[
            "taskId"
        ]
        self.assertEqual(self.get_result(task_id)["status"], "working")
        r = self.get_result(task_id)
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["error"], {"code": -32603, "message": "remote failure"})
        self.assertNotIn("result", r)

    def test_scripted_failed_default_message(self):
        r = self.get_result(self.call("task_scripted", {"outcome": "failed", "working_polls": 0})["taskId"])
        self.assertEqual(r["error"]["message"], "Task failed")

    def test_scripted_cancelled_by_server(self):
        r = self.get_result(
            self.call("task_scripted", {"outcome": "cancelled", "message": "shutting down", "working_polls": 0})[
                "taskId"
            ]
        )
        self.assertEqual(r["status"], "cancelled")
        self.assertEqual(r["statusMessage"], "shutting down")

    def test_scripted_tool_error(self):
        r = self.get_result(
            self.call("task_scripted", {"outcome": "tool_error", "message": "nope", "working_polls": 0})["taskId"]
        )
        self.assertEqual(r["status"], "completed")
        self.assertTrue(r["result"]["isError"])
        self.assertIn("nope", r["result"]["content"][0]["text"])

    def test_scripted_input_required(self):
        r = self.get_result(self.call("task_scripted", {"outcome": "input_required", "working_polls": 0})["taskId"])
        self.assertEqual(r["status"], "input_required")
        self.assertEqual(r["pollIntervalMs"], 1000)
        request = r["inputRequests"]["confirm"]
        self.assertEqual(request["method"], "elicitation/create")
        self.assertEqual(request["params"]["message"], "mcp-testkit: confirm to continue")
        self.assertEqual(request["params"]["requestedSchema"]["type"], "object")

    def test_scripted_input_required_message_is_the_elicitation_message(self):
        task_id = self.call("task_scripted", {"outcome": "input_required", "message": "Delete?", "working_polls": 0})[
            "taskId"
        ]
        self.assertEqual(self.get_result(task_id)["inputRequests"]["confirm"]["params"]["message"], "Delete?")

    def test_working_answer_has_no_input_requests(self):
        r = self.get_result(self.call("task_scripted", {"outcome": "input_required", "working_polls": 1})["taskId"])
        self.assertEqual(r["status"], "working")
        self.assertNotIn("inputRequests", r)

    def test_scripted_unsupported_status(self):
        r = self.get_result(
            self.call("task_scripted", {"outcome": "paused", "message": "why", "working_polls": 0})["taskId"]
        )
        self.assertEqual(r["status"], "paused")
        self.assertEqual(r["statusMessage"], "why")

    def test_scripted_working_never_finishes(self):
        task_id = self.call("task_scripted", {"outcome": "working", "working_polls": 0})["taskId"]
        for _ in range(5):
            self.assertEqual(self.get_result(task_id)["status"], "working")


class TestTaskCancel(_HttpCase):
    def test_cancel_running_task(self):
        task_id = self.call("task_scripted", {"working_polls": 5})["taskId"]
        self.assertEqual(self.get_result(task_id)["status"], "working")
        self.assertEqual(self.cancel(task_id)["result"]["resultType"], "complete")
        r = self.get_result(task_id)
        self.assertEqual(r["status"], "cancelled")
        self.assertEqual(r["statusMessage"], "Cancelled by client")
        self.assertEqual(self.get_result(task_id)["status"], "cancelled")

    def test_cancel_before_first_check(self):
        task_id = self.call("task_scripted", {"working_polls": 0})["taskId"]
        self.cancel(task_id)
        self.assertEqual(self.get_result(task_id)["status"], "cancelled")

    def test_cancel_input_required_task(self):
        task_id = self.call("task_scripted", {"outcome": "input_required", "working_polls": 0})["taskId"]
        self.assertEqual(self.get_result(task_id)["status"], "input_required")
        self.cancel(task_id)
        r = self.get_result(task_id)
        self.assertEqual(r["status"], "cancelled")
        self.assertNotIn("inputRequests", r)

    def test_cancel_finished_task_is_acknowledged_and_ignored(self):
        task_id = self.call("task_scripted", {"message": "done", "working_polls": 0})["taskId"]
        self.assertEqual(self.get_result(task_id)["status"], "completed")
        self.assertEqual(self.cancel(task_id)["result"]["resultType"], "complete")
        self.assertEqual(self.get_result(task_id)["status"], "completed")

    def test_cancel_twice(self):
        task_id = self.call("task_scripted", {"working_polls": 5})["taskId"]
        self.cancel(task_id)
        self.cancel(task_id)
        self.assertEqual(self.get_result(task_id)["status"], "cancelled")


class TestTaskUpdate(_HttpCase):
    def update(self, task_id, tasks=True):
        params = {"taskId": task_id, "inputResponses": {"answer": {"action": "accept", "content": {"x": 1}}}}
        return self.rpc("tasks/update", params, tasks=tasks, name=task_id)

    def test_update_is_acknowledged_and_changes_nothing(self):
        task_id = self.call("task_scripted", {"outcome": "input_required", "working_polls": 0})["taskId"]
        self.assertEqual(self.get_result(task_id)["status"], "input_required")
        self.assertEqual(self.update(task_id)["result"]["resultType"], "complete")
        self.assertEqual(self.get_result(task_id)["status"], "input_required")

    def test_update_unknown_task(self):
        self.assertEqual(self.update("task-999")["error"]["code"], -32602)

    def test_update_without_tasks_extension(self):
        task_id = self.call("task_scripted", {})["taskId"]
        self.assertEqual(self.update(task_id, tasks=False)["error"]["code"], -32021)


class TestTaskErrors(_HttpCase):
    def test_get_unknown_task(self):
        r = self.get("task-999")
        self.assertEqual(r["error"]["code"], -32602)
        self.assertIn("task-999", r["error"]["message"])

    def test_cancel_unknown_task(self):
        self.assertEqual(self.cancel("task-999")["error"]["code"], -32602)

    def test_get_without_tasks_extension(self):
        task_id = self.call("task_scripted", {})["taskId"]
        r = self.get(task_id, tasks=False)
        self.assertEqual(r["error"]["code"], -32021)
        self.assertEqual(r["error"]["data"]["requiredCapabilities"], {"extensions": {TASKS_EXTENSION: {}}})

    def test_cancel_without_tasks_extension(self):
        task_id = self.call("task_scripted", {"working_polls": 5})["taskId"]
        r = self.rpc("tasks/cancel", {"taskId": task_id}, tasks=False, name=task_id)
        self.assertEqual(r["error"]["code"], -32021)
        self.assertEqual(r["error"]["data"]["requiredCapabilities"], {"extensions": {TASKS_EXTENSION: {}}})
        self.assertEqual(self.get_result(task_id)["status"], "working", "the rejected cancel changed nothing")

    def test_get_without_task_id(self):
        r = self.rpc("tasks/get", {})
        self.assertEqual(r["error"]["code"], -32602)


class TestTaskResultLimit(_HttpCase):
    """A small limit (50 KiB) so the test stays fast; the default is 10 MiB."""

    def make_extension(self):
        return TasksExtension(max_result_bytes=50 * 1024)

    def test_result_within_limit_completes(self):
        args = {"tool": "echo_large", "arguments": {"size_kb": 20}, "working_polls": 0}
        r = self.get_result(self.call("task_run", args)["taskId"])
        self.assertEqual(r["status"], "completed")

    def test_result_over_limit_fails_with_a_clear_error(self):
        args = {"tool": "echo_large", "arguments": {"size_kb": 60}, "working_polls": 0}
        create = self.call("task_run", args)
        self.assertEqual(create["resultType"], "task")
        r = self.get_result(create["taskId"])
        self.assertEqual(r["status"], "failed")
        self.assertNotIn("result", r)
        self.assertEqual(r["error"]["code"], -32603)
        message = r["error"]["message"]
        self.assertIn("over the limit of 51,200 bytes that mcp-testkit keeps", message)
        self.assertIn("smaller echo_large size_kb", message)
        self.assertIn("--max-task-result-bytes", message)

    def test_default_limits(self):
        self.assertEqual(DEFAULT_MAX_RESULT_BYTES, 10 * 1024 * 1024)
        self.assertEqual(DEFAULT_MAX_TASKS, 1000)
        self.assertEqual(DEFAULT_TASK_TTL_MS, 3_600_000)

    def test_default_limit_rejects_a_large_echo(self):
        extension = TasksExtension()
        big = {"status": "completed", "result": {"content": [{"type": "text", "text": "x" * (10 * 1024 * 1024)}]}}
        self.assertEqual(extension._within_result_limit(big)["status"], "failed")


class _ExtensionContext:
    """The minimum request context for calling an extension method directly: a Tasks client."""

    protocol_version = "2026-07-28"

    class session:
        class client_capabilities:
            extensions = {TASKS_EXTENSION: {}}


class _StdioSession:
    """A real stdio server process, driven one JSON-RPC message at a time."""

    def __init__(self, test):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "mcp_test_server.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        test.addCleanup(self.close)
        self._next_id = 1

    def close(self):
        self.proc.kill()
        self.proc.wait()
        self.proc.stdin.close()
        self.proc.stdout.close()

    def notify(self, method):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params):
        request_id = self._next_id
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(self.proc.stdout, selectors.EVENT_READ)
            while True:
                if not selector.select(timeout=15):
                    raise TimeoutError(f"no response to {method}")
                response = json.loads(self.proc.stdout.readline())
                if response.get("id") == request_id:
                    return response

    def initialize(self, protocol_version, capabilities):
        params = {
            "protocolVersion": protocol_version,
            "capabilities": capabilities,
            "clientInfo": {"name": "test-client", "version": "1"},
        }
        response = self.request("initialize", params)
        self.notify("notifications/initialized")
        return response["result"]


class TestTaskStdioSessions(unittest.TestCase):
    """Backward compatibility: an older session never gets a task, even if it declares the extension."""

    LEGACY = "2025-11-25"
    TASKS = {"extensions": {TASKS_EXTENSION: {}}}

    def test_legacy_session_declaring_the_extension_gets_the_result_at_once(self):
        session = _StdioSession(self)
        init = session.initialize(self.LEGACY, self.TASKS)
        self.assertEqual(init["protocolVersion"], self.LEGACY)
        self.assertEqual(init["serverInfo"], {"name": "mcp-testkit", "version": __version__})
        self.assertNotIn("extensions", init["capabilities"], "the extension is not offered to a legacy session")
        r = session.request("tools/call", {"name": "task_scripted", "arguments": {"message": "done"}})
        self.assertNotIn("error", r)
        self.assertNotIn("taskId", r["result"])
        self.assertFalse(r["result"]["isError"])
        self.assertEqual(json.loads(r["result"]["content"][0]["text"]), {"result": "done"})

    def test_legacy_session_task_run_returns_the_tool_result(self):
        session = _StdioSession(self)
        session.initialize(self.LEGACY, self.TASKS)
        args = {"tool": "math_add", "arguments": {"a": 2, "b": 3}}
        r = session.request("tools/call", {"name": "task_run", "arguments": args})
        self.assertEqual(json.loads(r["result"]["content"][0]["text"]), {"result": 5.0})

    def test_legacy_session_scripted_failure_is_a_tool_error(self):
        session = _StdioSession(self)
        session.initialize(self.LEGACY, self.TASKS)
        r = session.request("tools/call", {"name": "task_scripted", "arguments": {"outcome": "failed", "message": "x"}})
        self.assertNotIn("error", r)
        self.assertTrue(r["result"]["isError"])

    def test_legacy_session_without_the_extension(self):
        session = _StdioSession(self)
        session.initialize(self.LEGACY, {})
        r = session.request("tools/call", {"name": "task_scripted", "arguments": {"message": "done"}})
        self.assertEqual(json.loads(r["result"]["content"][0]["text"]), {"result": "done"})

    def test_legacy_session_other_tools_unchanged(self):
        session = _StdioSession(self)
        session.initialize(self.LEGACY, self.TASKS)
        r = session.request("tools/call", {"name": "math_add", "arguments": {"a": 2, "b": 3}})
        self.assertEqual(json.loads(r["result"]["content"][0]["text"]), {"result": 5.0})

    def test_2026_request_over_stdio_still_creates_a_task(self):
        session = _StdioSession(self)
        params = {"name": "task_scripted", "arguments": {"message": "done", "working_polls": 0}, "_meta": _meta(True)}
        created = session.request("tools/call", params)["result"]
        self.assertEqual(created["resultType"], "task")
        task = session.request("tasks/get", {"taskId": created["taskId"], "_meta": _meta(True)})["result"]
        self.assertEqual(task["status"], "completed")
        self.assertEqual(json.loads(task["result"]["content"][0]["text"]), {"result": "done"})


class _FakeClock:
    """A monotonic clock that a test moves by hand."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


_OUTCOME = {"status": "completed", "result": {}}


class TestTaskStore(unittest.TestCase):
    def make(self, **limits):
        self.clock = _FakeClock()
        return TasksExtension(clock=self.clock, **limits)

    def lookup(self, extension, task_id):
        return extension._lookup(_ExtensionContext(), task_id)

    def test_task_is_readable_until_its_ttl_passes(self):
        extension = self.make(ttl_ms=1000)
        task = extension._create(0, 0, _OUTCOME)
        self.clock.now = 0.999
        self.assertIs(self.lookup(extension, task.task_id), task)
        self.clock.now = 1.0
        with self.assertRaises(MCPError) as ctx:
            self.lookup(extension, task.task_id)
        self.assertEqual(ctx.exception.error.code, -32602)
        self.assertEqual(list(extension._tasks), [])

    def test_finished_and_cancelled_tasks_stay_readable_until_the_ttl(self):
        extension = self.make(max_tasks=10)
        finished = extension._create(0, 0, _OUTCOME)
        finished.poll()
        cancelled = extension._create(100, 0, _OUTCOME)
        asyncio.run(extension._tasks_cancel(_ExtensionContext(), TaskIdParams(task_id=cancelled.task_id)))
        for _ in range(5):
            extension._create(0, 0, _OUTCOME)
        self.assertEqual(self.lookup(extension, finished.task_id).poll()["status"], "completed")
        self.assertEqual(self.lookup(extension, cancelled.task_id).poll()["status"], "cancelled")

    def test_full_store_refuses_a_new_task(self):
        extension = self.make(max_tasks=2, ttl_ms=5000)
        first = extension._create(0, 0, _OUTCOME)
        first.poll()
        extension._create(0, 0, _OUTCOME)
        with self.assertRaises(MCPError) as ctx:
            extension._create(0, 0, _OUTCOME)
        self.assertEqual(ctx.exception.error.code, -32603)
        message = ctx.exception.error.message
        self.assertIn("task store is full: 2 of 2 tasks (--max-tasks)", message)
        self.assertIn("removed 5,000 ms after it is created (--task-ttl-ms)", message)
        self.assertEqual(list(extension._tasks), ["task-1", "task-2"], "no task is evicted to make room")
        self.assertEqual(extension._next_id, 3, "a refused task uses no ID")

    def test_store_accepts_again_once_tasks_expire(self):
        extension = self.make(max_tasks=1, ttl_ms=1000)
        extension._create(100, 0, _OUTCOME)
        with self.assertRaises(MCPError):
            extension._create(0, 0, _OUTCOME)
        self.clock.now = 1.0
        self.assertEqual(extension._create(0, 0, _OUTCOME).task_id, "task-2")
        self.assertEqual(list(extension._tasks), ["task-2"])

    def test_abandoned_tasks_expire(self):
        """Tasks that never reach a terminal status, or that no one polls, are removed after the ttl."""
        extension = self.make(ttl_ms=1000)
        extension._create(100, 0, _OUTCOME)
        waiting = extension._create(0, 0, {"status": "input_required", "inputRequests": {}})
        waiting.poll()
        self.clock.now = 1.0
        extension._create(0, 0, _OUTCOME)
        self.assertEqual(list(extension._tasks), ["task-3"])

    def test_ttl_ms_is_sent_to_the_client(self):
        extension = self.make(ttl_ms=5000)
        self.assertEqual(extension._create(0, 0, _OUTCOME).view({"status": "working"})["ttlMs"], 5000)

    def test_configure_applies_to_new_tasks(self):
        extension = self.make(ttl_ms=1000)
        old = extension._create(0, 0, _OUTCOME)
        extension.configure(max_tasks=1000, max_result_bytes=1024, ttl_ms=5000)
        new = extension._create(0, 0, _OUTCOME)
        self.assertEqual((old.ttl_ms, new.ttl_ms), (1000, 5000))
        self.clock.now = 1.0
        self.assertEqual(list(extension._tasks), ["task-1", "task-2"])
        self.lookup(extension, new.task_id)
        self.assertEqual(list(extension._tasks), ["task-2"], "each task keeps the ttl it was created with")
        big = {"status": "completed", "result": {"text": "x" * 2000}}
        self.assertEqual(extension._within_result_limit(big)["status"], "failed")


class TestTaskStoreFullOverHttp(_HttpCase):
    def make_extension(self):
        return TasksExtension(max_tasks=1)

    def test_refused_task_is_a_clear_json_rpc_error(self):
        first = self.call("task_scripted", {"working_polls": 5})["taskId"]
        r = self.rpc("tools/call", {"name": "task_scripted", "arguments": {}}, name="task_scripted")
        self.assertEqual(r["error"]["code"], -32603)
        self.assertIn("task store is full", r["error"]["message"])
        self.assertEqual(self.get_result(first)["status"], "working", "the existing task is untouched")

    def test_cancelled_task_stays_readable_and_keeps_its_place(self):
        first = self.call("task_scripted", {"working_polls": 5})["taskId"]
        self.cancel(first)
        r = self.rpc("tools/call", {"name": "task_scripted", "arguments": {}}, name="task_scripted")
        self.assertEqual(r["error"]["code"], -32603)
        self.assertEqual(self.get_result(first)["status"], "cancelled")
        self.assertEqual(self.cancel(first)["result"]["resultType"], "complete")

    def test_other_tools_still_answer_when_the_store_is_full(self):
        self.call("task_scripted", {"working_polls": 5})
        r = self.call("math_add", {"a": 2, "b": 3})
        self.assertEqual(json.loads(r["content"][0]["text"]), {"result": 5.0})


if __name__ == "__main__":
    unittest.main()
