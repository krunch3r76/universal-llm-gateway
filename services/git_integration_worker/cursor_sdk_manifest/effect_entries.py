"""toolCall message parsing into ``EffectEntry`` rows and capture-branch classify.

Converts conversation steps and (branch B) MCP events into surface-addressable
effect entries. Invariant: ``classify_mcp_capture_branch`` is step-1 of capture
(A if any MCP toolCall, else B if repo/shell, else ``NO_CAPTURE``) and must not
raise on unparsed wire dicts — skip, do not fail. Depends on ``surface_taxonomy``
(op vocabularies), ``mcp_arguments`` (unwrap/detail), and ``cortex_surface``
(``_cortex_result_assertion_id`` only). Must not import ``manifest_build``
(that module calls into here).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from implement_admission.closeout_models import EffectEntry, SurfaceSection

from services.git_integration_worker.cursor_sdk_subagent_capture import (
    SUBAGENTS_SURFACE,
    entry_from_subagent_message,
    is_subagent_tool_call,
)

from . import cortex_surface, mcp_arguments, surface_taxonomy


def classify_mcp_capture_branch(turns: Iterable) -> surface_taxonomy.CaptureBranch:
    """Step 1: Branch A when conversation surfaces MCP toolCall steps."""
    saw_mcp = False
    saw_repo_or_shell = False
    for message in _iter_tool_call_messages(turns):
        tool_type = str(message.get("type") or "")
        if tool_type == surface_taxonomy._MCP_OP:
            saw_mcp = True
        if tool_type in surface_taxonomy._REPO_FILE_OPS or tool_type == surface_taxonomy._REPO_SHELL_OP:
            saw_repo_or_shell = True
    if saw_mcp:
        return "A"
    if saw_repo_or_shell:
        return "B"
    return "NO_CAPTURE"


def no_capture_degraded_reason(branch: surface_taxonomy.CaptureBranch) -> str | None:
    if branch == "NO_CAPTURE":
        return "no_capture_evidence"
    return None
def _iter_tool_call_messages(turns: Iterable) -> Iterable[Mapping[str, Any]]:
    for turn in turns or ():
        inner = getattr(turn, "turn", None)
        if inner is None and isinstance(turn, Mapping):
            inner = turn.get("turn")
        steps = getattr(inner, "steps", None) if inner is not None else None
        if steps is None and isinstance(inner, Mapping):
            steps = inner.get("steps")
        if not steps:
            continue
        for step in steps:
            step_type = (
                step.get("type")
                if isinstance(step, Mapping)
                else getattr(step, "type", None)
            )
            if step_type != "toolCall":
                continue
            message = (
                step.get("message")
                if isinstance(step, Mapping)
                else getattr(step, "message", None)
            )
            if isinstance(message, Mapping):
                yield message
def _entry_from_tool_call(message: Mapping[str, Any]) -> EffectEntry | None:
    tool_type = str(message.get("type") or "tool")
    args = message.get("args") if isinstance(message.get("args"), Mapping) else {}
    detail = mcp_arguments._bounded_detail(args)
    if tool_type == surface_taxonomy._MCP_OP:
        tool_name = str(args.get("toolName") or "mcp")
        nested = args.get("args") if isinstance(args.get("args"), Mapping) else {}
        effective = mcp_arguments._effective_mcp_args(nested)
        target = mcp_arguments._mcp_target(tool_name, effective)
        identity = mcp_arguments._mcp_identity(tool_name, effective)
        result = message.get("result")
        assertion_id = cortex_surface._cortex_result_assertion_id(tool_name, effective, result)
        if assertion_id is not None:
            identity = f"assertion:{assertion_id}"
        if tool_name == "dispatch":
            dispatched = mcp_arguments._string_arg(effective, "tool")
            merged_detail = dict(detail or {})
            merged_detail["opaque_dispatch"] = True
            if dispatched:
                merged_detail["dispatched_tool"] = dispatched
                target = dispatched
            detail = merged_detail
        elif tool_name in surface_taxonomy._FS_TOOLS:
            detail = mcp_arguments._fs_compact_detail(effective)
        return EffectEntry(
            op=tool_name,
            target=target,
            detail=detail,
            identity=identity,
        )
    if tool_type in surface_taxonomy._REPO_FILE_OPS:
        path = mcp_arguments._string_arg(args, "path", "filePath", "target")
        return EffectEntry(op=tool_type, target=path, detail=detail, identity=path)
    if tool_type == surface_taxonomy._REPO_SHELL_OP:
        command = mcp_arguments._string_arg(args, "command")
        return EffectEntry(op="shell", target=command, detail=detail, identity=command)
    if is_subagent_tool_call(tool_type=tool_type):
        return entry_from_subagent_message(message)
    return EffectEntry(op=tool_type, target=mcp_arguments._string_arg(args, "path"), detail=detail)


def _surface_for_tool_call(
    message: Mapping[str, Any], entry: EffectEntry
) -> str | None:
    tool_type = str(message.get("type") or "")
    if tool_type in surface_taxonomy._REPO_FILE_OPS or tool_type == surface_taxonomy._REPO_SHELL_OP:
        return "repo"
    if is_subagent_tool_call(tool_type=tool_type):
        return SUBAGENTS_SURFACE
    if tool_type != surface_taxonomy._MCP_OP:
        return None
    tool_name = entry.op
    if tool_name in surface_taxonomy._CORTEX_TOOLS:
        return "cortex"
    if tool_name in surface_taxonomy._AGENT_BUS_TOOLS:
        return "agent_bus"
    if tool_name in surface_taxonomy._FS_TOOLS:
        return "fs"
    if tool_name in surface_taxonomy._RAG_TOOLS:
        return "rag"
    if tool_name in surface_taxonomy._SERVICE_TOOLS:
        return "service"
    provider = str(
        (message.get("args") or {}).get("providerIdentifier")  # type: ignore[union-attr]
        if isinstance(message.get("args"), Mapping)
        else ""
    )
    if provider == surface_taxonomy._VORTEX_SERVER:
        return "service"
    return "service"


def _merge_mcp_event_entries(
    surfaces: dict[str, SurfaceSection],
    mcp_events: list[Mapping[str, Any]],
) -> None:
    for event in mcp_events:
        if not isinstance(event, Mapping):
            continue
        raw_payload = event.get("payload")
        payload = raw_payload if isinstance(raw_payload, Mapping) else event
        tool_name = str(payload.get("tool_name") or payload.get("toolName") or "mcp")
        surface = surface_taxonomy._surface_for_mcp_tool(tool_name)
        entry = EffectEntry(
            op=tool_name,
            target=str(payload.get("method") or payload.get("operation") or "") or None,
            detail=mcp_arguments._bounded_detail(payload),
            identity=str(payload.get("correlation_id") or "") or None,
        )
        section = surfaces[surface]
        surfaces[surface] = SurfaceSection(
            surface=surface,
            source="mcp_events",
            entries=[*section.entries, entry],
            cross_check=section.cross_check,
        )
