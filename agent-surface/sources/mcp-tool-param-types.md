<!-- target:* -->
# MCP Tool Parameter Types

## Invariant

**Invariant**: ∀ `@mcp.tool()` params: type ∈ {`str`, `int`, `float`, `bool`} for optional params.
¬`list[str]`, ¬`list[int]`, ¬`dict`, ¬`Any` as optional param types.

Claude.ai's MCP client silently drops optional params with `"type": "array"` or
`anyOf` JSON Schema from tool definitions. The params become invisible and
unusable — callers cannot pass them.

## Safe Types

| Type | Schema | Claude.ai | Use |
|---|---|---|---|
| `str = "web"` | `{"type": "string"}` | ✅ visible | All optional params |
| `int = 5` | `{"type": "integer"}` | ✅ visible | Numeric defaults |
| `float = 1.0` | `{"type": "number"}` | ✅ visible | Numeric defaults |
| `bool = False` | `{"type": "boolean"}` | ✅ visible | Flags |

## Unsafe Types (FORBIDDEN for optional params)

| Type | Schema | Claude.ai | Why |
|---|---|---|---|
| `list[str] = []` | `{"type": "array"}` | ❌ dropped | Optional array invisible |
| `list[str] \| None = None` | `anyOf[array, null]` | ❌ dropped | Union type dropped |
| `str \| None = None` | `anyOf[string, null]` | ⚠️ may drop | Union with null risky |
| `dict \| None = None` | `anyOf[object, null]` | ❌ dropped | Union type dropped |

Required array params (in JSON Schema `required` list) MAY work. Optional ones do not.

## Pattern: Lists as Comma-Separated Strings

```python
# ❌ Invisible to Claude.ai
@mcp.tool()
def my_tool(paths: list[str] = []) -> dict: ...

# ✅ Visible — parse comma-separated string internally
@mcp.tool()
def my_tool(paths: str = "") -> dict:
    path_list = [p.strip() for p in paths.split(",") if p.strip()] if paths else []
    ...
```

## Pattern: Optional Values as Empty Strings

```python
# ❌ Risky — anyOf schema
@mcp.tool()
def my_tool(scope: str | None = None) -> dict: ...

# ✅ Safe — empty string as sentinel
@mcp.tool()
def my_tool(scope: str = "") -> dict:
    effective_scope = scope or None  # normalize empty to None internally
    ...
```

## Exceptions

- Required params (`no default`) with `list` type may work but prefer `str` for consistency.
- Dispatch-style tools (`arguments: str = "{}"`) are safe — the JSON string carries all typed data.
<!-- /target:* -->
