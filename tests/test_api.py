"""Tests for the HTTP REST API endpoints."""

import contextlib
import io
import json
import sys
import unittest
from unittest import mock

from starlette.applications import Starlette
from starlette.testclient import TestClient

from mcp_test_server.api import ENDPOINTS, _get_openapi_spec, create_api_routes
from mcp_test_server.server import BearerAuthMiddleware, _build_http_app, _parse_args, main, mcp, tasks_extension


def _make_app(auth_key=None):
    """Create a Starlette test app with API routes."""
    routes = create_api_routes(mcp)
    app = Starlette(routes=routes)
    if auth_key:
        app.add_middleware(BearerAuthMiddleware, auth_key=auth_key)
    return app


class TestAPIEndpoints(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.client = TestClient(self.app)

    # --- Math (GET) ---
    def test_math_add(self):
        r = self.client.get("/api/math/add", params={"a": "3", "b": "5"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"result": 8.0})

    def test_math_divide_by_zero(self):
        r = self.client.get("/api/math/divide", params={"a": "10", "b": "0"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("error", r.json())

    def test_math_factorial(self):
        r = self.client.get("/api/math/factorial", params={"n": "5"})
        self.assertEqual(r.json(), {"result": 120})

    def test_math_fibonacci(self):
        r = self.client.get("/api/math/fibonacci", params={"n": "10"})
        self.assertEqual(r.json(), {"result": 55})

    # --- String (POST) ---
    def test_string_reverse(self):
        r = self.client.post("/api/string/reverse", json={"text": "hello"})
        self.assertEqual(r.json(), {"result": "olleh"})

    def test_string_split(self):
        r = self.client.post("/api/string/split", json={"text": "a,b,c", "delimiter": ","})
        self.assertEqual(r.json(), {"result": ["a", "b", "c"]})

    def test_string_join(self):
        r = self.client.post("/api/string/join", json={"items": ["a", "b"], "delimiter": "-"})
        self.assertEqual(r.json(), {"result": "a-b"})

    # --- Collection (POST) ---
    def test_collection_sort(self):
        r = self.client.post("/api/collection/sort", json={"items": [3, 1, 2]})
        self.assertEqual(r.json(), {"result": [1, 2, 3]})

    def test_collection_merge(self):
        r = self.client.post("/api/collection/merge", json={"dict_a": {"a": 1}, "dict_b": {"b": 2}})
        self.assertEqual(r.json(), {"result": {"a": 1, "b": 2}})

    def test_collection_chunk(self):
        r = self.client.post("/api/collection/chunk", json={"items": [1, 2, 3, 4, 5], "size": 2})
        self.assertEqual(r.json(), {"result": [[1, 2], [3, 4], [5]]})

    # --- Encoding (POST) ---
    def test_encoding_base64_roundtrip(self):
        r = self.client.post("/api/encoding/base64-encode", json={"text": "hello"})
        self.assertEqual(r.json(), {"result": "aGVsbG8="})
        r = self.client.post("/api/encoding/base64-decode", json={"data": "aGVsbG8="})
        self.assertEqual(r.json(), {"result": "hello"})

    def test_encoding_sha256(self):
        r = self.client.post("/api/encoding/sha256", json={"text": "hello"})
        self.assertEqual(r.json()["result"], "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")

    # --- DateTime (POST) ---
    def test_datetime_day_of_week(self):
        r = self.client.post("/api/datetime/day-of-week", json={"date_string": "2024-03-15"})
        self.assertEqual(r.json(), {"result": "Friday"})

    def test_datetime_is_leap_year(self):
        r = self.client.post("/api/datetime/is-leap-year", json={"year": 2024})
        self.assertEqual(r.json(), {"result": True})

    # --- Validation (POST) ---
    def test_validation_is_email(self):
        r = self.client.post("/api/validation/is-email", json={"text": "user@example.com"})
        self.assertTrue(r.json()["valid"])

    def test_validation_is_json(self):
        r = self.client.post("/api/validation/is-json", json={"text": '{"key":"val"}'})
        self.assertTrue(r.json()["valid"])

    # --- Conversion (GET) ---
    def test_conversion_celsius_to_fahrenheit(self):
        r = self.client.get("/api/conversion/celsius-to-fahrenheit", params={"value": "100"})
        self.assertEqual(r.json(), {"result": 212.0})

    def test_conversion_rgb_to_hex(self):
        r = self.client.get("/api/conversion/rgb-to-hex", params={"r": "255", "g": "128", "b": "0"})
        self.assertEqual(r.json(), {"result": "#ff8000"})

    def test_conversion_hex_to_rgb(self):
        r = self.client.get("/api/conversion/hex-to-rgb", params={"hex_color": "ff8000"})
        self.assertEqual(r.json(), {"result": {"r": 255, "g": 128, "b": 0}})

    # --- Echo (mixed) ---
    def test_echo(self):
        r = self.client.post("/api/echo", json={"message": "hi"})
        self.assertEqual(r.json(), {"result": "hi"})

    def test_echo_error(self):
        r = self.client.post("/api/echo/error", json={"message": "fail"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("error", r.json())

    def test_echo_types(self):
        r = self.client.get("/api/echo/types")
        data = r.json()
        self.assertEqual(data["string"], "hello")
        self.assertEqual(data["integer"], 42)
        self.assertIsNone(data["null"])

    def test_echo_nested(self):
        r = self.client.get("/api/echo/nested", params={"depth": "2"})
        data = r.json()["result"]
        self.assertEqual(data["level"], 0)
        self.assertEqual(data["child"]["level"], 1)

    def test_echo_empty(self):
        r = self.client.get("/api/echo/empty")
        self.assertEqual(r.json(), {"result": ""})

    def test_echo_multiple(self):
        r = self.client.post("/api/echo/multiple", json={"messages": ["a", "b", "c"]})
        self.assertEqual(r.json(), {"results": ["a", "b", "c"]})

    def test_echo_schema(self):
        body = {
            "str_param": "hello",
            "int_param": 42,
            "float_param": 3.14,
            "bool_param": True,
            "list_param": [1, 2],
            "obj_param": {"k": "v"},
        }
        r = self.client.post("/api/echo/schema", json=body)
        self.assertEqual(r.json(), body)

    # --- Task (POST, synchronous) ---
    def test_task_run(self):
        r = self.client.post("/api/task/run", json={"tool": "math_add", "arguments": {"a": 2, "b": 3}})
        self.assertEqual(r.json(), {"result": 5.0})

    def test_task_run_unknown_tool(self):
        r = self.client.post("/api/task/run", json={"tool": "no_such_tool"})
        self.assertIn("error", r.json())

    def test_task_run_nested_tool_error(self):
        r = self.client.post("/api/task/run", json={"tool": "echo_error", "arguments": {"message": "x"}})
        self.assertEqual(r.json(), {"error": "Error executing tool echo_error: Intentional error: x"})

    def test_task_scripted_completed(self):
        r = self.client.post("/api/task/scripted", json={"outcome": "completed", "message": "done"})
        self.assertEqual(r.json(), {"result": "done"})

    def test_task_scripted_failed(self):
        r = self.client.post("/api/task/scripted", json={"outcome": "failed", "message": "boom"})
        self.assertIn("boom", r.json()["error"])

    # --- Weather (GET) ---
    def test_weather(self):
        r = self.client.get("/api/weather", params={"city": "San Francisco"})
        data = r.json()
        self.assertEqual(data["city"], "San Francisco")
        self.assertEqual(data["temperature_f"], 77)
        self.assertEqual(data["condition"], "sunny")

    def test_weather_deterministic(self):
        r1 = self.client.get("/api/weather", params={"city": "NYC"})
        r2 = self.client.get("/api/weather", params={"city": "NYC"})
        self.assertEqual(r1.json(), r2.json())


class TestAPIAuth(unittest.TestCase):
    def setUp(self):
        self.app = _make_app(auth_key="test_secret")
        self.client = TestClient(self.app)

    def test_no_auth_returns_401(self):
        r = self.client.get("/api/math/add", params={"a": "1", "b": "2"})
        self.assertEqual(r.status_code, 401)

    def test_wrong_auth_returns_401(self):
        r = self.client.get("/api/math/add", params={"a": "1", "b": "2"}, headers={"Authorization": "Bearer wrong"})
        self.assertEqual(r.status_code, 401)

    def test_correct_auth_returns_200(self):
        r = self.client.get(
            "/api/math/add", params={"a": "1", "b": "2"}, headers={"Authorization": "Bearer test_secret"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"result": 3.0})

    def test_post_with_auth(self):
        r = self.client.post(
            "/api/string/reverse", json={"text": "abc"}, headers={"Authorization": "Bearer test_secret"}
        )
        self.assertEqual(r.json(), {"result": "cba"})

    def test_api_docs_requires_auth(self):
        r = self.client.get("/api-docs")
        self.assertEqual(r.status_code, 401)

    def test_api_docs_with_auth(self):
        r = self.client.get("/api-docs", headers={"Authorization": "Bearer test_secret"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["openapi"], "3.0.3")


def _string_length_call(size):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "string_length", "arguments": {"text": "x" * size}},
    }
    return json.dumps(body)


class TestTaskOptions(unittest.TestCase):
    """--max-tasks, --max-task-result-bytes and --task-ttl-ms configure the task store."""

    def test_defaults(self):
        args = _parse_args([])
        self.assertEqual(
            (args.max_tasks, args.max_task_result_bytes, args.task_ttl_ms), (1000, 10 * 1024 * 1024, 3_600_000)
        )

    def test_options_set_the_values(self):
        args = _parse_args(["--max-tasks", "5", "--max-task-result-bytes", "2048", "--task-ttl-ms", "60000"])
        self.assertEqual((args.max_tasks, args.max_task_result_bytes, args.task_ttl_ms), (5, 2048, 60000))

    def test_options_reject_invalid_values(self):
        for option in ("--max-tasks", "--max-task-result-bytes", "--task-ttl-ms"):
            for value in ("0", "-1", "abc"):
                stderr = io.StringIO()
                with self.subTest(option=option, value=value), contextlib.redirect_stderr(stderr):
                    with self.assertRaises(SystemExit):
                        _parse_args([option, value])
                    self.assertIn(f"{option}: must be a positive integer, got '{value}'", stderr.getvalue())

    def test_main_configures_the_task_store(self):
        argv = ["mcp-testkit", "--max-tasks", "7", "--max-task-result-bytes", "4096", "--task-ttl-ms", "9000"]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(mcp, "run"),
            mock.patch.object(tasks_extension, "configure") as configure,
        ):
            main()
        configure.assert_called_once_with(max_tasks=7, max_result_bytes=4096, ttl_ms=9000)


class TestRequestBodyLimit(unittest.TestCase):
    """/mcp keeps the SDK's 4 MiB body limit (GHSA-fmmv-w9g8-j3gc) unless --max-request-body-size raises it."""

    HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

    def post(self, max_request_body_size, size):
        kwargs = {} if max_request_body_size is None else {"max_request_body_size": max_request_body_size}
        app = _build_http_app(**kwargs)
        with TestClient(app, base_url="http://127.0.0.1:3001") as client:
            return client.post("/mcp", content=_string_length_call(size), headers=self.HEADERS)

    def test_default_option_is_the_sdk_default(self):
        self.assertEqual(_parse_args([]).max_request_body_size, 4 * 1024 * 1024)

    def test_option_sets_the_limit(self):
        self.assertEqual(_parse_args(["--max-request-body-size", "104857600"]).max_request_body_size, 104857600)

    def test_option_rejects_invalid_values(self):
        for value in ("0", "-1", "abc"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    _parse_args(["--max-request-body-size", value])

    def test_default_limit_rejects_a_5_mb_request(self):
        r = self.post(None, 5_000_000)
        self.assertEqual(r.status_code, 413)

    def test_raised_limit_accepts_a_5_mb_request(self):
        r = self.post(8 * 1024 * 1024, 5_000_000)
        self.assertEqual(r.status_code, 200)
        self.assertIn('{\\"result\\": 5000000}', r.text)

    def test_small_limit_still_serves_small_requests(self):
        self.assertEqual(self.post(1000, 10).status_code, 200)
        self.assertEqual(self.post(1000, 2000).status_code, 413)


class TestOpenAPISpec(unittest.TestCase):
    def setUp(self):
        self.spec = _get_openapi_spec()

    def test_spec_version(self):
        self.assertEqual(self.spec["openapi"], "3.0.3")

    def test_spec_paths_match_endpoints(self):
        # /api-docs is a meta-endpoint and is not in the spec.
        self.assertEqual(set(self.spec["paths"]), {path for path, *_ in ENDPOINTS})

    def test_all_endpoints_in_spec(self):
        for path, _method, _tool, _summary, _params in ENDPOINTS:
            self.assertIn(path, self.spec["paths"], f"Missing path: {path}")

    def test_get_endpoints_have_parameters(self):
        for path, method, _tool, _summary, params in ENDPOINTS:
            if method == "GET" and params:
                op = self.spec["paths"][path]["get"]
                self.assertIn("parameters", op, f"GET {path} missing parameters")

    def test_post_endpoints_have_request_body(self):
        for path, method, _tool, _summary, params in ENDPOINTS:
            if method == "POST" and params:
                op = self.spec["paths"][path]["post"]
                self.assertIn("requestBody", op, f"POST {path} missing requestBody")

    def test_spec_has_security_scheme(self):
        self.assertIn("BearerAuth", self.spec["components"]["securitySchemes"])

    def test_at_least_two_thirds_post(self):
        post_count = sum(1 for _, m, *_ in ENDPOINTS if m == "POST")
        self.assertGreaterEqual(
            post_count / len(ENDPOINTS), 2 / 3, f"Only {post_count}/{len(ENDPOINTS)} POST endpoints"
        )


if __name__ == "__main__":
    unittest.main()
