<!-- target:* -->
# JSON Schema (llama.cpp / GGUF)

**Constrained decoding via `json_schema` → GBNF grammar**, for llama.cpp GGUF models via `response_format`.

Source: [json-schema-to-grammar.cpp](https://github.com/ggml-org/llama.cpp/blob/master/common/json-schema-to-grammar.cpp)

## Invariants
∀ `_call_model(..., json_schema=)`: schema MUST be explicit.
`json_schema={"type": "object"}` ⟹ **unconstrained** (matches any JSON object) ⟹ ❌ FORBIDDEN.

∀ arrays with known count: MUST include `minItems`/`maxItems` to prevent premature closure.

## Wire Format
```python
# In _call_model, json_schema is converted to:
response_format = {"type": "json_object", "schema": <json_schema>}
```

## Supported Features

| Feature | Example | Notes |
|---|---|---|
| `type` | `"string"`, `"integer"`, `"number"`, `"boolean"`, `"null"`, `"object"`, `"array"` | Multi-type via array: `["string", "null"]` |
| `properties` | `{"a": {"type": "string"}}` | Named object fields |
| `required` | `["a", "b"]` | Required property names |
| `additionalProperties` | `false` or `{"type": "string"}` | Restrict/type extra keys |
| `enum` | `["math", "general"]` | Constrains to literal values |
| `const` | `"fixed_value"` | Single constant |
| `items` | `{"type": "string"}` | Homogeneous array items |
| `prefixItems` | `[{"type": "string"}, {"type": "int"}]` | Tuple types |
| `minItems` / `maxItems` | `3` / `10` | Array length bounds |
| `minimum` / `maximum` | `-10` / `100` | Integer bounds only (`exclusiveMinimum`/`exclusiveMaximum` also) |
| `minLength` / `maxLength` | `1` / `50` | String length bounds |
| `pattern` | `"^[a-z]+$"` | Regex (MUST be `^...$` anchored) |
| `format` | `"uuid"`, `"date"`, `"time"`, `"date-time"` | String format validation |
| `oneOf` / `anyOf` | Union types | Treated identically |
| `allOf` | Intersection | Merges properties; intersects enums |
| `$ref` | `"#/definitions/foo"` | Local JSON pointer refs; HTTPS remote refs |

## NOT Supported

❌ `not`, `if/then/else`, `dependentRequired`, `dependentSchemas`
❌ `patternProperties`, `propertyNames`, `contains`, `uniqueItems`
❌ `multipleOf`, `minimum`/`maximum` for `number` (only `integer`)
❌ `$dynamicRef`, `$anchor`, `unevaluatedProperties`

## Pattern: Explicit Schema

```python
# ✅ Correct — grammar constrains output structure
json_schema={
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {"type": "string"},
        }
    },
    "required": ["claims"],
}

# ❌ Forbidden — no structural constraint, grammar accepts any object
json_schema={"type": "object"}
```

## Pattern: Array Count Enforcement

**Invariant**: ∀ arrays with known count at call time: MUST include `minItems`/`maxItems`.

Without count constraints, the GBNF grammar allows the model to close the array
prematurely. When the model emits EOS early, the grammar forces valid JSON brackets,
producing syntactically valid but semantically incomplete output (e.g., 1 evaluation
instead of 2).

```python
# ✅ Correct — grammar requires exactly N items before allowing array closure
json_schema={
    "type": "object",
    "properties": {
        "evaluations": {
            "type": "array",
            "minItems": len(chunk_items),  # Known at call time
            "maxItems": len(chunk_items),
            "items": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "boolean"},
                    "reasoning": {"type": "string"},
                },
                "required": ["verdict", "reasoning"],
            },
        }
    },
    "required": ["evaluations"],
}

# ❌ Forbidden — model can close array after any number of items
json_schema={
    "type": "object",
    "properties": {
        "evaluations": {
            "type": "array",
            # Missing minItems/maxItems — premature EOS allowed
            "items": {...},
        }
    },
}
```

**When to omit `minItems`/`maxItems`:**
- Variable-length output where count is unknown (e.g., claim decomposition)
- Arrays that genuinely accept any length (e.g., optional reasoning steps)

## Caveat: Silent Fallback
If schema→grammar conversion succeeds but grammar parsing fails,
llama-server logs the error but generates **unconstrained** output
(returns HTTP 200). Monitor logs for grammar parse failures.
<!-- /target:* -->
