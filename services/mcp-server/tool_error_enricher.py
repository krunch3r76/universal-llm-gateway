"""Tool arg validation error enricher for the MCP server.

Catches pydantic.ValidationError from FastMCP tool-arg validation and
returns an agent-actionable structured envelope (accepted_params,
required_params, expected_type, hints) so the model can self-correct.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import mcp.types as mt
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp_events import record
from pydantic import ValidationError
from universal_logging import get_logger

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)


class ToolErrorEnricher(Middleware):
    """Outermost middleware that rewrites ValidationError into structured error envelope."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        try:
            return await call_next(context)
        except ValidationError as exc:
            tool_name = getattr(context.message, "name", "<unknown>")

            schema: dict[str, Any] = {}
            try:
                if context.fastmcp_context and context.fastmcp_context.fastmcp:
                    tool_obj = await context.fastmcp_context.fastmcp.get_tool(tool_name)
                    if tool_obj is not None:
                        schema = getattr(tool_obj, "parameters", {}) or {}
            except Exception:
                # lookup failure must never poison the error path
                pass

            errors: list[dict[str, Any]] = []
            for err in exc.errors():
                loc = err.get("loc") or ()
                param = str(loc[0]) if loc else "?"
                input_val = err.get("input")
                input_str = str(input_val) if input_val is not None else ""
                input_type = type(input_val).__name__ if input_val is not None else "NoneType"
                entry: dict[str, Any] = {
                    "type": err.get("type", "unknown"),
                    "param": param,
                    "msg": err.get("msg", ""),
                    "input": input_str,
                    "input_type": input_type,
                }
                if entry["type"] == "unexpected_keyword_argument":
                    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
                    accepted = sorted(k for k in props if isinstance(k, str))
                    required = schema.get("required", []) if isinstance(schema, dict) else []
                    if not isinstance(required, list):
                        required = []
                    entry["accepted_params"] = accepted
                    entry["required_params"] = required
                    acc = ", ".join(accepted) if accepted else "none"
                    entry["hint"] = f"'{param}' is not a parameter of '{tool_name}'. Accepted: {acc}."
                else:
                    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
                    prop_schema = props.get(param, {}) if isinstance(props, dict) else {}
                    exp_type = prop_schema.get("type") if isinstance(prop_schema, dict) else None
                    if exp_type:
                        entry["expected_type"] = exp_type
                    type_hint = exp_type or "valid input"
                    entry["hint"] = f"'{param}' expects {type_hint}; received {input_type}."
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
            )
            return ToolResult(structured_content=envelope)


def register_tool_error_enricher(mcp: FastMCP) -> None:
    """Register ToolErrorEnricher as outermost middleware (before ResponseSizeGuard)."""
    mcp.add_middleware(ToolErrorEnricher())
