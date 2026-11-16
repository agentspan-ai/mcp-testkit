# MCP Test Server

A Python MCP server with **deterministic tools** in groups, supporting **stdio**, **HTTP**, and **REST API** transports. Built for consistent, repeatable MCP protocol testing.

It is a test utility, not a production server: simple, predictable, and with clear errors that name the limit you hit and the option that changes it.

## Install

mcp-testkit is a command-line server, so install it in its own environment:

```bash
pipx install mcp-testkit
# or
uv tool install mcp-testkit
```

`pip install mcp-testkit` also works. It needs `mcp` 2.3.0 or later, so it cannot share an environment with a package that needs `mcp` 1.x. Your MCP client does not need to share an environment with the server: they talk only over stdio or HTTP.

Or from source:

```bash
git clone https://github.com/agentspan-ai/mcp-testkit.git
cd mcp-testkit
pip install -e .
```

## Quick Start

```bash
# stdio (default)
mcp-testkit

# Streamable HTTP (/mcp) + REST API on port 3001
mcp-testkit --transport http

# With bearer token authentication
mcp-testkit --transport http --auth super_secret_key

# Custom host/port
mcp-testkit --transport http --host 0.0.0.0 --port 8080
```

## Transports

| Transport | Command | Endpoints |
|-----------|---------|-----------|
| **stdio** | `mcp-testkit` | MCP JSON-RPC over stdin/stdout |
| **HTTP** | `mcp-testkit --transport http` | MCP at `/mcp` + REST at `/api/*` + OpenAPI at `/api-docs` |

## Authentication

Pass `--auth <key>` to require a Bearer token on all requests (both MCP at `/mcp` and REST):

```bash
mcp-testkit --transport http --auth my_secret

# Clients must include:
# Authorization: Bearer my_secret
```

## Request size limit

`/mcp` rejects request bodies over **4 MiB** with HTTP `413`. This is the MCP SDK default, added as a security fix ([GHSA-fmmv-w9g8-j3gc](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-fmmv-w9g8-j3gc)): without a limit, the server read whole request bodies into memory, so one large request could exhaust it.

To test larger requests, raise it:

```bash
mcp-testkit --transport http --max-request-body-size 104857600   # 100 MiB
```

Keep the default on a server that clients you do not trust can reach, especially without `--auth`. Responses (for example `echo_large`) and the REST API at `/api/*` are not limited by this setting.

## Task limits

The [MCP Tasks](#mcp-tasks-extension) store has three options:

| Option | Default | What it does |
|--------|---------|--------------|
| `--max-tasks N` | `1000` | Most tasks kept at once. When the store is full, a new task is refused. |
| `--max-task-result-bytes BYTES` | `10485760` (10 MiB) | Largest result one task keeps, as JSON. A larger result makes the task fail. |
| `--task-ttl-ms MS` | `3600000` (1 hour) | How long a task stays readable after it is created. It is also the `ttlMs` that clients get. |

```bash
mcp-testkit --transport http --max-tasks 5000 --task-ttl-ms 600000
```

The server keeps tasks in memory, so large values use more memory.

## Host and Origin checks

`/mcp` checks the `Host` and `Origin` headers against `--host`, to protect against DNS rebinding (an MCP SDK feature):

- With the default `--host 127.0.0.1` (or `localhost` or `::1`), `/mcp` accepts only `Host: 127.0.0.1:*`, `localhost:*` or `[::1]:*`. Any other `Host` gets HTTP `421`, and a browser `Origin` from another site gets `403`.
- With any other `--host`, for example `0.0.0.0`, `/mcp` does not check these headers.

To reach the server by another name (for example from a Docker container through `host.docker.internal`), start it with `--host 0.0.0.0`. The REST API at `/api/*` does not check these headers.

## Tools

All tools are **deterministic** — same input always produces same output. No randomness, no current time, no external state. The one exception is the task store behind the [MCP Tasks](#mcp-tasks-extension) tools.

### Math

| Tool | Params | Description |
|------|--------|-------------|
| `math_add` | `a`, `b` | Add two numbers |
| `math_subtract` | `a`, `b` | Subtract b from a |
| `math_multiply` | `a`, `b` | Multiply two numbers |
| `math_divide` | `a`, `b` | Divide (errors on zero) |
| `math_modulo` | `a`, `b` | Remainder (errors on zero) |
| `math_power` | `base`, `exponent` | Exponentiation |
| `math_factorial` | `n` | n! (errors on negative) |
| `math_fibonacci` | `n` | Nth Fibonacci number (0-indexed) |

### String

| Tool | Params | Description |
|------|--------|-------------|
| `string_reverse` | `text` | Reverse a string |
| `string_uppercase` | `text` | Convert to uppercase |
| `string_lowercase` | `text` | Convert to lowercase |
| `string_length` | `text` | Character count |
| `string_char_count` | `text`, `char` | Count occurrences |
| `string_replace` | `text`, `old`, `new` | Replace all occurrences |
| `string_split` | `text`, `delimiter` | Split by delimiter |
| `string_join` | `items`, `delimiter` | Join list with delimiter |

### Collection

| Tool | Params | Description |
|------|--------|-------------|
| `collection_sort` | `items`, `reverse?` | Sort a list |
| `collection_flatten` | `items` | Recursively flatten nested lists |
| `collection_merge` | `dict_a`, `dict_b` | Merge dicts (b wins on conflict) |
| `collection_filter_gt` | `items`, `threshold` | Filter numbers > threshold |
| `collection_unique` | `items` | Remove duplicates (order preserved) |
| `collection_group_by` | `items`, `key` | Group objects by key |
| `collection_zip` | `list_a`, `list_b` | Zip into list of pairs |
| `collection_chunk` | `items`, `size` | Split into chunks |

### Encoding

| Tool | Params | Description |
|------|--------|-------------|
| `encoding_base64_encode` | `text` | Base64 encode |
| `encoding_base64_decode` | `data` | Base64 decode |
| `encoding_url_encode` | `text` | URL-encode |
| `encoding_url_decode` | `text` | URL-decode |
| `encoding_hex_encode` | `text` | Hex encode |
| `encoding_hex_decode` | `data` | Hex decode |
| `encoding_md5` | `text` | MD5 hash |
| `encoding_sha256` | `text` | SHA-256 hash |

### DateTime

All operate on provided dates — never use current time.

| Tool | Params | Description |
|------|--------|-------------|
| `datetime_parse` | `date_string` | Parse ISO date to components |
| `datetime_format` | `year`, `month`, `day`, `format` | Format date to string |
| `datetime_add_days` | `date_string`, `days` | Add/subtract days |
| `datetime_diff` | `date_a`, `date_b` | Days between two dates |
| `datetime_day_of_week` | `date_string` | Weekday name |
| `datetime_is_leap_year` | `year` | Leap year check |
| `datetime_days_in_month` | `year`, `month` | Days in month |
| `datetime_week_number` | `date_string` | ISO week number |

### Validation

All return `{valid: bool, reason: string}`.

| Tool | Params | Description |
|------|--------|-------------|
| `validation_is_email` | `text` | Email format check |
| `validation_is_url` | `text` | URL format check |
| `validation_is_ipv4` | `text` | IPv4 address check |
| `validation_is_ipv6` | `text` | IPv6 address check |
| `validation_is_uuid` | `text` | UUID format check |
| `validation_is_json` | `text` | Valid JSON check |
| `validation_is_palindrome` | `text` | Palindrome check |
| `validation_matches_regex` | `text`, `pattern` | Regex match check |

### Conversion

| Tool | Params | Description |
|------|--------|-------------|
| `conversion_celsius_to_fahrenheit` | `value` | C to F |
| `conversion_fahrenheit_to_celsius` | `value` | F to C |
| `conversion_km_to_miles` | `value` | km to miles |
| `conversion_miles_to_km` | `value` | miles to km |
| `conversion_bytes_to_human` | `bytes` | Bytes to human string |
| `conversion_rgb_to_hex` | `r`, `g`, `b` | RGB to hex color |
| `conversion_hex_to_rgb` | `hex_color` | Hex to RGB |
| `conversion_decimal_to_binary` | `value` | Decimal to binary string |

### Echo / Protocol Testing

| Tool | Params | Description |
|------|--------|-------------|
| `echo` | `message` | Echo input unchanged |
| `echo_error` | `message` | Always raises ToolError |
| `echo_large` | `size_kb` | Deterministic ~N KB text |
| `echo_nested` | `depth` | Nested JSON to depth N |
| `echo_types` | — | All JSON types |
| `echo_empty` | — | Empty string |
| `echo_multiple` | `messages` | Multiple TextContent blocks |
| `echo_schema` | `str_param`, `int_param`, `float_param`, `bool_param`, `list_param`, `obj_param` | Complex schema test |

### Standalone

| Tool | Params | Description |
|------|--------|-------------|
| `get_weather` | `city` | Fixed response: 77°F, sunny, always |

### Task

| Tool | Params | Description |
|------|--------|-------------|
| `task_run` | `tool`, `arguments`?, `working_polls`?, `poll_interval_ms`? | Run another tool as an MCP task |
| `task_scripted` | `outcome`?, `message`?, `working_polls`?, `poll_interval_ms`? | Scripted task outcome |

See [MCP Tasks extension](#mcp-tasks-extension).

## MCP Tasks extension

The server implements the MCP Tasks extension (`io.modelcontextprotocol/tasks`, protocol `2026-07-28`): `tools/call` can return a `CreateTaskResult`, and the client polls it with `tasks/get` and can stop it with `tasks/cancel`. `tasks/update` is acknowledged.

A call runs as a task only when **all** are true:

- the tool is `task_run` or `task_scripted`,
- the request uses protocol `2026-07-28` or later, and
- the request declares the extension in `params._meta["io.modelcontextprotocol/clientCapabilities"].extensions`.

An older session (for example `2025-11-25` over stdio, after `initialize`) always gets the synchronous result, even if it declared the extension in `initialize`: that protocol cannot carry a task result, and the server does not offer the extension to it.

Every other call answers at once. That includes all other tools, called by a Tasks client. A client without the extension, and the REST API, run `task_run` and `task_scripted` synchronously.

Each task is scripted by its arguments, so a test controls every step. `tasks/get` reports `working` for `working_polls` checks (default 2), then the final outcome.

| Call | Final `tasks/get` answer |
|------|--------------------------|
| `task_run` `{tool, arguments}` | `completed`, `result` = that tool's `CallToolResult` (a tool error is `result.isError: true`) |
| `task_scripted` `outcome: "completed"` | `completed`, `result` = `{"result": message}` |
| `task_scripted` `outcome: "tool_error"` | `completed`, `result.isError: true` |
| `task_scripted` `outcome: "failed"` | `failed`, `error: {code: -32603, message}` |
| `task_scripted` `outcome: "cancelled"` | `cancelled`, `statusMessage` = message |
| `task_scripted` `outcome: "input_required"` | `input_required`, `inputRequests` = one fixed `elicitation/create` request under key `confirm` (`message` is its text). The task stays `input_required` until `tasks/cancel` |
| `task_scripted` any other `outcome` | that status as given (for example `working` never finishes, or an unsupported status) |

Details:

- **Task IDs** are `task-1`, `task-2`, ... in creation order, per server process. All clients share this sequence, so tests that run in parallel against one server (for example `pytest -n 3`) get interleaved IDs. Always use the `taskId` that `tools/call` returns; never hard-code a task ID.
- **Lifetime and limits:** a task stays readable for its `ttlMs` after it is created, whatever its status, and whether or not the client read or cancelled it. Then it is removed, and its ID gives `-32602`. The store holds up to `--max-tasks` tasks. When it is full, a new task is refused with JSON-RPC error `-32603` ("task store is full"); other tools still answer. A result over `--max-task-result-bytes` as JSON is not kept: the task ends `failed`, with an error that names the size and the limit. `content` and `structuredContent` both carry a tool's text, so `echo_large` of N KB is about 2N KB as JSON.
- **`pollIntervalMs`** is `poll_interval_ms` (default 1000) on every non-terminal answer. `0` omits it.
- **`ttlMs`** is `--task-ttl-ms`. Expiry uses the server's monotonic clock, which never appears in a response.
- **`createdAt` / `lastUpdatedAt`** (ISO-8601, required by the extension) come from a logical clock, not the current time: task N is created at `2026-01-01T00:00:00Z` + N seconds, and each `tasks/get` advances `lastUpdatedAt` by one second until the check that reports the final outcome. After that it no longer changes.
- **`tasks/cancel`** acknowledges with `{"resultType": "complete"}`. A task that has not finished becomes `cancelled` with `statusMessage: "Cancelled by client"`. A finished task is left as it is.
- **`tasks/update`** acknowledges with `{"resultType": "complete"}` and changes nothing. The answer to an `input_required` task's `confirm` request is ignored too: the task does not resume. The task must exist.
- **Errors:** an unknown or expired task ID gives `-32602` (on `tasks/get`, `tasks/cancel` and `tasks/update`). A `tasks/*` request that does not declare the extension gives `-32021` with `requiredCapabilities`.

Example (HTTP, stateless `2026-07-28` request):

```bash
META='"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientInfo":{"name":"curl","version":"1"},"io.modelcontextprotocol/clientCapabilities":{"extensions":{"io.modelcontextprotocol/tasks":{}}}}'
H=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2026-07-28')

curl -s localhost:3001/mcp "${H[@]}" -H 'Mcp-Method: tools/call' -H 'Mcp-Name: task_run' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"task_run","arguments":{"tool":"math_add","arguments":{"a":2,"b":3}},'"$META"'}}'
# result: {"resultType":"task","taskId":"task-1","status":"working","pollIntervalMs":1000,...}

curl -s localhost:3001/mcp "${H[@]}" -H 'Mcp-Method: tasks/get' -H 'Mcp-Name: task-1' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tasks/get","params":{"taskId":"task-1",'"$META"'}}'
# working, working, then: {"status":"completed","result":{"content":[{"type":"text","text":"{\"result\": 5.0}"}],...}}
```

## REST API

When running with `--transport http`, all tools are also available as HTTP endpoints:

```bash
# GET endpoints (math, conversion, some echo, weather)
curl "http://localhost:3001/api/math/add?a=3&b=5"
# {"result": 8.0}

curl "http://localhost:3001/api/weather?city=NYC"
# {"city":"NYC","temperature_f":77,...}

# POST endpoints (string, collection, encoding, datetime, validation, some echo)
curl -X POST "http://localhost:3001/api/string/reverse" \
  -H "Content-Type: application/json" \
  -d '{"text":"hello"}'
# {"result": "olleh"}

curl -X POST "http://localhost:3001/api/encoding/sha256" \
  -H "Content-Type: application/json" \
  -d '{"text":"hello"}'
# {"result": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"}
```

**OpenAPI spec** at `GET /api-docs` (OpenAPI 3.0.3).

With auth enabled, include `Authorization: Bearer <key>` on all requests.

## Testing

```bash
python3 -m pytest tests/ -v
```

Tests cover all tools, the MCP Tasks extension, REST API endpoints, auth, OpenAPI spec, and integration.

## Project Structure

```
mcp-testkit/
├── pyproject.toml              # Package config, deps, CLI entry point
├── mcp_test_server/
│   ├── __init__.py             # Package version
│   ├── server.py               # Entry point — MCP server + REST API + auth
│   ├── api.py                  # REST API routes + OpenAPI spec generation
│   └── tools/
│       ├── __init__.py         # Tool group registry
│       ├── math_tools.py
│       ├── string_tools.py
│       ├── collection_tools.py
│       ├── encoding_tools.py
│       ├── datetime_tools.py
│       ├── validation_tools.py
│       ├── conversion_tools.py
│       ├── echo_tools.py      # Includes get_weather
│       └── task_tools.py      # task_* tools + MCP Tasks extension
└── tests/
    ├── test_math_tools.py
    ├── test_string_tools.py
    ├── test_collection_tools.py
    ├── test_encoding_tools.py
    ├── test_datetime_tools.py
    ├── test_validation_tools.py
    ├── test_conversion_tools.py
    ├── test_echo_tools.py
    ├── test_task_tools.py
    ├── test_api.py
    ├── test_integration.py
    └── test_e2e.py             # Starts a real server process
```
