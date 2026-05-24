"""Dynamic registration of flat MCP tools for /mcp/grok (B2).

∀ canonical tool T ∈ derive_grok_manifest():
  ∃ registered tool in grok_mcp with T.name and T.inputSchema.

Tools are thin dispatchers — same underlying implementations as /mcp.
Routing per dispatcher_call_shape.tool domain:
  cortex/agent_bus  → aggregator(tool=dispatch_value, arguments=json.dumps(params))
  grokbuild         → grokbuild_fn(op=dispatch_value, **params)
  manage            → manage_fn(action=dispatch_value, **params)
  observability     → observability_fn(operation=dispatch_value, params=params)
  pipeline          → pipeline_fn(op=dispatch_value, **params)
  rag               → rag_fn(op=dispatch_value, arguments=json.dumps(params))
  fs                → fs_fn(op=dispatch_value, **params)
  dispatch          → special per-tool routing (overflow/frontier/team)
  tool_search       → registered separately (post-prune in main server)
  retrieve          → direct from pre_prune_tool_objects
"""

from __future__ import annotations

import json
import keyword
from typing import TYPE_CHECKING, Any

from fastmcp.tools import FunctionTool
from universal_logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastmcp import FastMCP

logger = get_logger(__name__)

# ── dispatch type strings ────────────────────────────────────────────────────
# Maps dispatcher_call_shape.tool → how to forward the flat tool call.
_DOMAIN_DISPATCH: dict[str, str] = {
    "cortex": "tool_arguments",  # aggregator(tool=dv, arguments=json.dumps(params))
    "agent_bus": "tool_arguments",  # aggregator(tool=dv, arguments=json.dumps(params))
    "rag": "rag_op_arguments",  # rag(op=dv, arguments=json.dumps(params))
    "grokbuild": "op_kwargs",  # aggregator(op=dv, **params)
    "pipeline": "op_kwargs",  # aggregator(op=dv, **params)
    "fs": "op_kwargs",  # aggregator(op=dv, **params)
    "manage": "action_kwargs",  # aggregator(action=dv, **params)
    "observability": "operation_params",  # aggregator(operation=dv, params=params)
    "dispatch": "dispatch_special",  # per-canonical-name routing
    "tool_search": "direct",  # already registered in caller
    "retrieve": "direct",  # already registered in caller
}


def _make_dispatch_fn(
    dispatch_type: str,
    dispatch_value: str,
    aggregator_fn: Callable[..., Any],
) -> Callable[..., Any]:
    """Return a callable that dispatches params to the aggregator per dispatch_type."""

    def _tool_arguments(**params: Any) -> Any:
        return aggregator_fn(tool=dispatch_value, arguments=json.dumps(params))

    def _rag_op_arguments(**params: Any) -> Any:
        return aggregator_fn(op=dispatch_value, arguments=json.dumps(params))

    def _op_kwargs(**params: Any) -> Any:
        return aggregator_fn(op=dispatch_value, **params)

    def _action_kwargs(**params: Any) -> Any:
        return aggregator_fn(action=dispatch_value, **params)

    def _operation_params(**params: Any) -> Any:
        return aggregator_fn(operation=dispatch_value, params=params)

    _dispatch_map = {
        "tool_arguments": _tool_arguments,
        "rag_op_arguments": _rag_op_arguments,
        "op_kwargs": _op_kwargs,
        "action_kwargs": _action_kwargs,
        "operation_params": _operation_params,
    }
    return _dispatch_map[dispatch_type]


def _safe_param_name(name: str) -> str:
    """Map a JSON property name to a safe Python identifier.

    Special case: the agent_bus 'from' wire field is exposed as the documented
    'from_agent' to match _reply_dispatch / _post_dispatch signatures and all
    public documentation. Other keywords get the trailing underscore.
    """
    if name == "from":
        return "from_agent"
    if keyword.iskeyword(name) or keyword.issoftkeyword(name):
        return f"{name}_"
    return name


def _build_fn_with_schema(
    tool_name: str,
    description: str,
    json_schema: dict[str, Any],
    dispatch_fn: Callable[..., Any],
) -> FunctionTool:
    """Build a FunctionTool with canonical json_schema and dispatch_fn as implementation.

    Uses exec to generate a function with the correct positional/optional parameters
    so FastMCP can parse the call site. The parameters are then overridden with the
    canonical json_schema for wire-accurate inputSchema.

    Python reserved keywords in property names (e.g. 'from') are aliased with
    a trailing underscore in the function signature and remapped in the dispatch call.
    """
    props = json_schema.get("properties", {})
    req_set = set(json_schema.get("required", []))

    # Map JSON property names → safe Python identifiers
    alias: dict[str, str] = {p: _safe_param_name(p) for p in props}

    # Build parameter list: required first (no default), optional last (default=None).
    # Required params must come before optional to satisfy Python syntax.
    param_parts: list[str] = []
    for pname in props:
        safe = alias[pname]
        if pname in req_set:
            param_parts.append(f"{safe}: object")
    for pname in props:
        safe = alias[pname]
        if pname not in req_set:
            param_parts.append(f"{safe}: object = None")

    params_str = ", ".join(param_parts)
    # Build dispatch kwargs dict: remap safe names back to original JSON keys.
    kw_items = ", ".join(f'"{pname}": {alias[pname]}' for pname in props)
    props_repr = "{" + kw_items + "}"
    func_code = (
        f"def {tool_name}({params_str}) -> object:\n"
        f"    _kw = {{k: v for k, v in {props_repr}.items() if v is not None}}\n"
        f"    return _dispatch(**_kw)\n"
    )
    ns: dict[str, Any] = {"_dispatch": dispatch_fn}
    exec(func_code, ns)  # noqa: S102 — controlled inputs from canonical.yaml
    fn = ns[tool_name]

    tool = FunctionTool.from_function(fn, name=tool_name, description=description)
    # Override the schema derived from the Python sig with the canonical json_schema.
    return tool.model_copy(update={"parameters": json_schema})


def register_grok_flat_tools(
    mcp: FastMCP,
    pre_prune_tool_objects: dict[str, Any],
    canonical_manifest: list[dict[str, Any]],
    canonical_raw: dict[str, Any],
) -> tuple[int, list[str]]:
    """Register all canonical flat tools in the grok FastMCP server.

    For tools already present in pre_prune_tool_objects under their canonical name,
    the existing Tool object is reused. For others, a thin dispatch wrapper is
    created from the canonical json_schema + dispatcher_call_shape.

    Returns (registered_count, missing_tools) where missing_tools is the list of
    canonical names that could not be registered due to missing aggregator fns.
    """
    # Build index: canonical_name → full registry entry (for dispatcher_call_shape)
    raw_tools: dict[str, dict[str, Any]] = {
        t["canonical_name"]: t for t in canonical_raw.get("tools", [])
    }

    # Pre-index aggregator functions from pre_prune_tool_objects.
    # These are Tool objects; we need their .fn attribute to call them.
    def _get_fn(name: str) -> Callable[..., Any] | None:
        obj = pre_prune_tool_objects.get(name)
        return obj.fn if obj is not None else None

    registered: list[str] = []
    missing: list[str] = []

    for entry in canonical_manifest:
        canonical_name = entry["canonical_name"]
        tool_name = entry["name"]  # flat MCP tool name (flat_call_shape.tool)
        description = entry.get("description", "")
        json_schema = entry.get("inputSchema", {})

        # Case 1: tool already in pre_prune_tool_objects under exact canonical name
        if tool_name in pre_prune_tool_objects and tool_name != "tool_search":
            tool_obj = pre_prune_tool_objects[tool_name]
            # Override parameters with canonical schema for wire accuracy.
            mcp.add_tool(tool_obj.model_copy(update={"parameters": json_schema}))
            registered.append(tool_name)
            continue

        # Case 2: tool_search — caller handles separately; skip here.
        if tool_name == "tool_search":
            registered.append(tool_name)
            continue

        # Case 3: dispatch wrapper from dispatcher_call_shape
        raw_entry = raw_tools.get(canonical_name, {})
        dcs = raw_entry.get("dispatcher_call_shape", {})
        dispatcher_tool = dcs.get("tool", "")
        dispatch_value = dcs.get("dispatch_value", "")
        dispatch_type = _DOMAIN_DISPATCH.get(dispatcher_tool, "")

        if not dispatch_type or not dispatcher_tool:
            logger.warning(
                "grok_flat: no dispatch type for %s (domain=%s) — skipped",
                canonical_name,
                dispatcher_tool,
            )
            missing.append(tool_name)
            continue

        # Special handling for dispatch_* tools (dispatch_overflow/frontier/team)
        if dispatch_type == "dispatch_special":
            tool = _build_dispatch_special_tool(
                tool_name,
                description,
                json_schema,
                dispatch_value,
                pre_prune_tool_objects,
            )
            if tool is None:
                missing.append(tool_name)
                continue
            mcp.add_tool(tool)
            registered.append(tool_name)
            continue

        aggregator_fn = _get_fn(dispatcher_tool)
        if aggregator_fn is None:
            logger.warning(
                "grok_flat: aggregator %r not found for %s — skipped",
                dispatcher_tool,
                canonical_name,
            )
            missing.append(tool_name)
            continue

        dispatch_fn = _make_dispatch_fn(dispatch_type, dispatch_value, aggregator_fn)
        tool = _build_fn_with_schema(tool_name, description, json_schema, dispatch_fn)
        mcp.add_tool(tool)
        registered.append(tool_name)

    return len(registered), missing


def _build_dispatch_special_tool(
    tool_name: str,
    description: str,
    json_schema: dict[str, Any],
    dispatch_value: str,
    pre_prune_tool_objects: dict[str, Any],
) -> FunctionTool | None:
    """Build dispatch_overflow / dispatch_frontier / dispatch_team tools.

    dispatch_overflow → calls the 'dispatch' aggregator (tool + arguments).
    dispatch_frontier / dispatch_team → call the named overflow tool directly.
    """

    def _get_fn(name: str) -> Callable[..., Any] | None:
        obj = pre_prune_tool_objects.get(name)
        return obj.fn if obj is not None else None

    if dispatch_value == "overflow":
        # dispatch_overflow(tool, arguments) → dispatch(tool=tool, arguments=arguments)
        dispatch_fn = _get_fn("dispatch")
        if dispatch_fn is None:
            return None

        # This tool's schema already has 'tool' and 'arguments' fields — pass through.
        def _dispatch_overflow_impl(**params: Any) -> Any:
            return dispatch_fn(
                tool=params.get("tool", ""),
                arguments=params.get("arguments", "{}"),
            )

        return _build_fn_with_schema(
            tool_name, description, json_schema, _dispatch_overflow_impl
        )

    # dispatch_frontier → frontier_dispatch(model, prompt, system, timeout_s)
    # dispatch_team → team_dispatch(role, body)
    # These exist as individual tools in the overflow.
    direct_name = f"{dispatch_value}_dispatch"  # 'frontier' → 'frontier_dispatch'
    direct_fn = _get_fn(direct_name)
    if direct_fn is None:
        logger.warning(
            "grok_flat: %r not found for dispatch_%s — skipped",
            direct_name,
            dispatch_value,
        )
        return None

    def _direct_dispatch(**params: Any) -> Any:
        return direct_fn(**params)

    return _build_fn_with_schema(tool_name, description, json_schema, _direct_dispatch)
