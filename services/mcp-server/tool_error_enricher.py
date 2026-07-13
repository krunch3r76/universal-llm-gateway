"""Tool arg validation error enricher for the MCP server.

Catches pydantic.ValidationError from FastMCP tool-arg validation and
returns an agent-actionable structured envelope (accepted_params,
required_params, expected_type, hints) so the model can self-correct.

Signal 5: mcp.tool.validation.error events include first_invocation_this_session:
  True  → model invoked this tool blind (first call, schema never seen)
  False → schema worked before; this is a regression or model drift
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

import mcp.types as mt
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp_events import record
from pydantic import ValidationError
from request_profile import current_request_metadata
from universal_logging import get_logger

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

_FS_AMBIGUOUS_PREFIXES = frozenset(
    {"notes", "tasks", "tmp", "agent-skills", "services"}
)

_FS_SANDBOX_HINT = (
    "sandbox is required when path has no Share URI scheme. "
    "Use workspaces://{repo}/{rel} or cortex://{rel}, "
    "or pass sandbox=cortex|workspaces explicitly."
)


def _known_workspaces_repo_names() -> set[str]:
    try:
        from tools._project_paths import project_root, repo_roots

        return {repo.name for repo in repo_roots(project_root())}
    except Exception:
        return set()


def _call_payload_path(payload: Any) -> str:
    if isinstance(payload, dict):
        raw = payload.get("path")
        if raw is not None:
            return str(raw).strip()
    return ""


def fs_missing_sandbox_hint(path: str = "") -> str:
    """Advisory hint when fs is called without sandbox (no inference)."""
    parts = [part for part in path.strip().strip("/").split("/") if part]
    if parts:
        first = parts[0]
        if first in _known_workspaces_repo_names():
            return (
                f"{_FS_SANDBOX_HINT} Path advisory: looks like workspaces; "
                "pass sandbox=workspaces or use workspaces://{repo}/..."
            )
        if first in _FS_AMBIGUOUS_PREFIXES:
            return (
                f"{_FS_SANDBOX_HINT} Path advisory: ambiguous — this path shape "
                "exists under BOTH stores; use cortex:// or workspaces:// scheme."
            )
    return f"{_FS_SANDBOX_HINT}"


def life_workspaces_fs_refusal() -> dict[str, str]:
    """Explicit /mcp/life boundary when callers target the workspaces sandbox."""
    return {
        "error": (
            "sandbox='workspaces' is not available on the /mcp/life surface. "
            "Repository source reads and edits are served on /mcp/code only. "
            "For agent-process artifacts (specs, packets, closeouts, sidecars), "
            "use sandbox='cortex' or a cortex:// Share URI. "
            "Life-seat handoff packets must carry a cortex:// sidecar mirror."
        )
    }


# Per-session invocation tracker for Signal 5 (first_invocation_this_session).
# Maps (session_id, tool_name) → True once we've seen a call for that pair.
# Bounded LRU to avoid unbounded memory growth across many sessions.
_SESSION_TOOL_LOCK = threading.Lock()
_SESSION_TOOL_SEEN: OrderedDict[tuple[str, str], bool] = OrderedDict()
_SESSION_TOOL_MAX = 4096  # max distinct (session, tool) pairs tracked


def _record_invocation(session_id: str, tool_name: str) -> bool:
    """Record invocation; return True if this is the first call for (session, tool).

    Thread-safe. Evicts oldest entries when capacity is reached.
    """
    key = (session_id, tool_name)
    with _SESSION_TOOL_LOCK:
        if key in _SESSION_TOOL_SEEN:
            _SESSION_TOOL_SEEN.move_to_end(key)
            return False
        _SESSION_TOOL_SEEN[key] = True
        while len(_SESSION_TOOL_SEEN) > _SESSION_TOOL_MAX:
            _SESSION_TOOL_SEEN.popitem(last=False)
        return True


class ToolErrorEnricher(Middleware):
    """Outermost middleware that rewrites ValidationError into structured error envelope."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool_name = getattr(context.message, "name", "<unknown>")

        # Signal 5: track invocation before call to capture first-call status.
        # session_id comes from mcp_session_id injected by McpRequestEventsMiddleware.
        meta = current_request_metadata()
        session_id = str(meta.get("mcp_session_id") or "")
        first_invocation = _record_invocation(session_id, tool_name)

        try:
            return await call_next(context)
        except ValidationError as exc:
            schema: dict[str, Any] = {}
            try:
                if context.fastmcp_context and context.fastmcp_context.fastmcp:
                    tool_obj = await context.fastmcp_context.fastmcp.get_tool(tool_name)
                    if tool_obj is not None:
                        schema = getattr(tool_obj, "parameters", {}) or {}
            except Exception as exc:
                # lookup failure must never poison the error path
                logger.debug(
                    "ToolErrorEnricher schema lookup failed for %s: %s", tool_name, exc
                )

            schema_props_raw = (
                schema.get("properties", {}) if isinstance(schema, dict) else {}
            )
            schema_props = (
                schema_props_raw if isinstance(schema_props_raw, dict) else {}
            )
            tool_param_names = {k for k in schema_props if isinstance(k, str)}

            # Heuristic: enrich only errors that look like they came from the
            # FastMCP tool-arg validation boundary. If no error loc matches a
            # tool parameter name AND no error type is arg-boundary-specific,
            # the ValidationError almost certainly came from inside the tool
            # body (e.g. an internal Model.model_validate call); enriching it
            # with the tool's schema would emit misleading hints (the tool's
            # parameter list would be listed as accepted_params for an
            # internal Model error). Re-raise and let the tool's own
            # try/except — or the generic _tool_error_envelope downstream —
            # handle it.
            arg_boundary_types = {
                "unexpected_keyword_argument",
                "missing",
                "missing_argument",
            }
            err_first_locs: set[str] = set()
            err_types: set[str] = set()
            for e in exc.errors():
                loc_seq = e.get("loc") or ()
                if loc_seq:
                    err_first_locs.add(str(loc_seq[0]))
                err_types.add(str(e.get("type", "")))
            looks_like_arg_boundary = bool(
                (err_first_locs & tool_param_names) or (err_types & arg_boundary_types)
            )
            if not looks_like_arg_boundary:
                raise

            errors: list[dict[str, Any]] = []
            for err in exc.errors():
                loc = err.get("loc") or ()
                param = str(loc[0]) if loc else "?"
                err_type = str(err.get("type", "unknown"))
                input_val = err.get("input")
                # For missing/missing_argument, pydantic puts the entire call
                # payload in err["input"]. Describing that payload's type as
                # the "received" type is misleading — the agent didn't send
                # a value for the missing param at all. Override here so the
                # downstream hint reflects the actual structural cause.
                if err_type in {"missing", "missing_argument"}:
                    input_str = ""
                    input_type = "<missing>"
                else:
                    input_str = str(input_val) if input_val is not None else ""
                    input_type = (
                        type(input_val).__name__
                        if input_val is not None
                        else "NoneType"
                    )
                entry: dict[str, Any] = {
                    "type": err_type,
                    "param": param,
                    "msg": err.get("msg", ""),
                    "input": input_str,
                    "input_type": input_type,
                }
                if err_type == "unexpected_keyword_argument":
                    accepted = sorted(tool_param_names)
                    required_raw = (
                        schema.get("required", []) if isinstance(schema, dict) else []
                    )
                    required = required_raw if isinstance(required_raw, list) else []
                    entry["accepted_params"] = accepted
                    entry["required_params"] = required
                    acc = ", ".join(accepted) if accepted else "none"
                    entry["hint"] = (
                        f"'{param}' is not a parameter of '{tool_name}'. "
                        f"Accepted: {acc}."
                    )
                elif err_type in {"missing", "missing_argument"}:
                    prop_schema_raw = schema_props.get(param, {})
                    prop_schema = (
                        prop_schema_raw if isinstance(prop_schema_raw, dict) else {}
                    )
                    exp_type = prop_schema.get("type")
                    if exp_type:
                        entry["expected_type"] = exp_type
                    type_hint = exp_type or "valid input"
                    if tool_name == "fs" and param == "sandbox":
                        entry["hint"] = fs_missing_sandbox_hint(
                            _call_payload_path(err.get("input"))
                        )
                    else:
                        entry["hint"] = f"'{param}' is required (expects {type_hint})."
                else:
                    prop_schema_raw = schema_props.get(param, {})
                    prop_schema = (
                        prop_schema_raw if isinstance(prop_schema_raw, dict) else {}
                    )
                    exp_type = prop_schema.get("type")
                    if exp_type:
                        entry["expected_type"] = exp_type
                    type_hint = exp_type or "valid input"
                    entry["hint"] = (
                        f"'{param}' expects {type_hint}; received {input_type}."
                    )
                errors.append(entry)

            envelope: dict[str, Any] = {
                "error": str(exc),
                "error_type": "ValidationError",
                "tool": tool_name,
                "errors": errors,
            }
            record(
                "mcp.tool.validation.error",
                tool=tool_name,
                error_count=len(errors),
                error_types=sorted({e.get("type", "") for e in errors}),
                first_invocation_this_session=first_invocation,
            )
            return ToolResult(structured_content=envelope, is_error=True)


def register_tool_error_enricher(mcp: FastMCP) -> None:
    """Register ToolErrorEnricher as outermost middleware (before ResponseSizeGuard)."""
    mcp.add_middleware(ToolErrorEnricher())
