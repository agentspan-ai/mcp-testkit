# MCP Test Server Design

## Overview

A Python MCP server supporting both stdio and SSE transports, providing 64 deterministic tools across 8 groups for MCP protocol testing.

## Constraints

- **Dependencies**: `mcp` SDK, `requests`, Python stdlib only
- **Determinism**: All tools produce identical output for identical input. No randomness, no current time, no external state.
- **Transports**: stdio and SSE

## Architecture

Modular package with explicit registration. Each tool group is a separate module exporting a `register(server)` function.

```
mcp-test-server/
├── server.py              # Entry point — creates MCP server, registers all tools, runs stdio/SSE
├── tools/
│   ├── __init__.py
│   ├── math_tools.py
│   ├── string_tools.py
│   ├── collection_tools.py
│   ├── encoding_tools.py
│   ├── datetime_tools.py
│   ├── validation_tools.py
│   ├── conversion_tools.py
│   └── echo_tools.py
└── requirements.txt       # mcp, requests
```

## server.py

- Creates an MCP `Server` instance
- Imports and calls `register(server)` from each tool module
- CLI with `--transport` flag: `stdio` (default) or `sse`
- For SSE: runs HTTP server on configurable `--host`/`--port`

## Tool Groups

### 1. Math (math_tools.py)

| Tool | Params | Returns |
|------|--------|---------|
| `math_add` | `a: number, b: number` | Sum |
| `math_subtract` | `a: number, b: number` | Difference |
| `math_multiply` | `a: number, b: number` | Product |
| `math_divide` | `a: number, b: number` | Quotient (error on zero) |
| `math_modulo` | `a: number, b: number` | Remainder |
| `math_power` | `base: number, exponent: number` | Power |
| `math_factorial` | `n: integer` | n! (error on negative/large) |
| `math_fibonacci` | `n: integer` | Nth Fibonacci number |

### 2. String (string_tools.py)

| Tool | Params | Returns |
|------|--------|---------|
| `string_reverse` | `text: string` | Reversed string |
| `string_uppercase` | `text: string` | Uppercased string |
| `string_lowercase` | `text: string` | Lowercased string |
| `string_length` | `text: string` | Character count |
| `string_char_count` | `text: string, char: string` | Occurrences of char |
| `string_replace` | `text: string, old: string, new: string` | Replaced string |
| `string_split` | `text: string, delimiter: string` | List of parts |
| `string_join` | `items: string[], delimiter: string` | Joined string |

### 3. Collection (collection_tools.py)

| Tool | Params | Returns |
|------|--------|---------|
| `collection_sort` | `items: array, reverse: bool=false` | Sorted list |
| `collection_flatten` | `items: array` | Flattened list |
| `collection_merge` | `dict_a: object, dict_b: object` | Merged dict (b wins) |
| `collection_filter_gt` | `items: number[], threshold: number` | Filtered list |
| `collection_unique` | `items: array` | Deduplicated list (order preserved) |
| `collection_group_by` | `items: object[], key: string` | Grouped dict |
| `collection_zip` | `list_a: array, list_b: array` | List of pairs |
| `collection_chunk` | `items: array, size: integer` | List of chunks |

### 4. Encoding (encoding_tools.py)

| Tool | Params | Returns |
|------|--------|---------|
| `encoding_base64_encode` | `text: string` | Base64 string |
| `encoding_base64_decode` | `data: string` | Decoded string |
| `encoding_url_encode` | `text: string` | URL-encoded string |
| `encoding_url_decode` | `text: string` | URL-decoded string |
| `encoding_hex_encode` | `text: string` | Hex string |
| `encoding_hex_decode` | `data: string` | Decoded string |
| `encoding_md5` | `text: string` | MD5 hex digest |
| `encoding_sha256` | `text: string` | SHA-256 hex digest |

### 5. DateTime (datetime_tools.py)

All operate on provided dates, never use current time.

| Tool | Params | Returns |
|------|--------|---------|
| `datetime_parse` | `date_string: string` | `{year, month, day, hour, minute, second}` |
| `datetime_format` | `year: int, month: int, day: int, format: string` | Formatted string |
| `datetime_add_days` | `date_string: string, days: int` | New ISO date string |
| `datetime_diff` | `date_a: string, date_b: string` | Days between (integer) |
| `datetime_day_of_week` | `date_string: string` | Weekday name |
| `datetime_is_leap_year` | `year: int` | Boolean |
| `datetime_days_in_month` | `year: int, month: int` | Integer |
| `datetime_week_number` | `date_string: string` | ISO week number |

### 6. Validation (validation_tools.py)

All return `{valid: bool, reason: string}`.

| Tool | Params | Returns |
|------|--------|---------|
| `validation_is_email` | `text: string` | Email format check |
| `validation_is_url` | `text: string` | URL format check |
| `validation_is_ipv4` | `text: string` | IPv4 check |
| `validation_is_ipv6` | `text: string` | IPv6 check |
| `validation_is_uuid` | `text: string` | UUID format check |
| `validation_is_json` | `text: string` | Valid JSON check |
| `validation_is_palindrome` | `text: string` | Palindrome check |
| `validation_matches_regex` | `text: string, pattern: string` | Regex match check |

### 7. Conversion (conversion_tools.py)

| Tool | Params | Returns |
|------|--------|---------|
| `conversion_celsius_to_fahrenheit` | `value: number` | Fahrenheit |
| `conversion_fahrenheit_to_celsius` | `value: number` | Celsius |
| `conversion_km_to_miles` | `value: number` | Miles |
| `conversion_miles_to_km` | `value: number` | Kilometers |
| `conversion_bytes_to_human` | `bytes: integer` | Human-readable string |
| `conversion_rgb_to_hex` | `r: int, g: int, b: int` | Hex color string |
| `conversion_hex_to_rgb` | `hex_color: string` | `{r, g, b}` |
| `conversion_decimal_to_binary` | `value: integer` | Binary string |

### 8. Echo (echo_tools.py)

MCP protocol edge-case testing.

| Tool | Params | Returns |
|------|--------|---------|
| `echo` | `message: string` | Input unchanged |
| `echo_error` | `message: string` | Always raises McpError |
| `echo_large` | `size_kb: integer` | Deterministic text of ~N KB |
| `echo_nested` | `depth: integer` | Nested JSON to given depth |
| `echo_types` | (none) | Object with all JSON types |
| `echo_empty` | (none) | Empty string |
| `echo_multiple` | `messages: string[]` | Multiple TextContent blocks |
| `echo_schema` | `str_param: str, int_param: int, float_param: float, bool_param: bool, list_param: list, obj_param: dict` | All params echoed back |

## Error Handling

- Tools return structured JSON responses as text content
- Invalid inputs return clear error messages via `McpError` or structured error objects
- `math_divide` with zero divisor, `math_factorial` with negative input, etc. return errors
- `echo_error` always raises to test client error handling

## Response Format

All tools return JSON-serialized results as `TextContent`. Example:

```json
{"result": 42}
```

For validation tools:
```json
{"valid": true, "reason": "Valid email format"}
```
