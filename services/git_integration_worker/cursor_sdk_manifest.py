"""First-party multi-surface effects manifest for cursor-sdk closeout."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)

from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet

CaptureBranch = Literal["A", "B", "NO_CAPTURE"]
DetailCap = 500
ResultCap = 2000
MAX_MANIFEST_BODY_PROBE = 4_000

_REPO_FILE_OPS = frozenset({"write", "edit", "delete"})
_REPO_SHELL_OP = "shell"
_MCP_OP = "mcp"
_VORTEX_SERVER = "user-vortex"

_CORTEX_TOOLS = frozenset({"cortex", "cortex_boot"})
_AGENT_BUS_TOOLS = frozenset({"agent_bus", "agent_bus_read"})
_FS_TOOLS = frozenset({"fs"})
_RAG_TOOLS = frozenset({"rag"})
_SERVICE_TOOLS = frozenset(
    {
        "manage",
        "pipeline",
        "observability",
        "team_dispatch",
        "panel_dispatch",
        "skill_suggest",
        "retrieve",
        "tool_search",
        "dispatch",
    }
)
_SURFACE_ORDER = ("repo", "cortex", "agent_bus", "fs", "rag", "service")


def classify_mcp_capture_branch(turns: Iterable) -> CaptureBranch:
    """Step 1: Branch A when conversation surfaces MCP toolCall steps."""
    saw_mcp = False
    saw_repo_or_shell = False
    for message in _iter_tool_call_messages(turns):
        tool_type = str(message.get("type") or "")
        if tool_type == _MCP_OP:
            saw_mcp = True
        if tool_type in _REPO_FILE_OPS or tool_type == _REPO_SHELL_OP:
            saw_repo_or_shell = True
    if saw_mcp:
        return "A"
    if saw_repo_or_shell:
        return "B"
    return "NO_CAPTURE"


def no_capture_degraded_reason(branch: CaptureBranch) -> str | None:
    if branch == "NO_CAPTURE":
        return "no_capture_evidence"
    return None


def build_effects_manifest(
    *,
    dispatch_id: str,
    thread_id: str,
    turns: Iterable,
    mcp_events: list[Mapping[str, Any]] | None = None,
    wrapper_effects: dict[str, list[EffectEntry]] | None = None,
    capture_branch: CaptureBranch | None = None,
) -> EffectsManifest:
    """Pure manifest builder — never raises on unparsed wire dicts."""
    branch = capture_branch or classify_mcp_capture_branch(turns)
    sources: list[str] = ["conversation"]
    surfaces: dict[str, SurfaceSection] = {
        name: SurfaceSection(surface=name, source="conversation", entries=[])
        for name in _SURFACE_ORDER
    }

    for message in _iter_tool_call_messages(turns):
        entry = _entry_from_tool_call(message)
        if entry is None:
            continue
        surface = _surface_for_tool_call(message, entry)
        if surface is None:
            continue
        surfaces[surface].entries.append(entry)

    if branch == "B" and mcp_events:
        sources.append("mcp_events")
        _merge_mcp_event_entries(surfaces, mcp_events)

    if wrapper_effects:
        sources.append("wrapper")
        for surface, entries in wrapper_effects.items():
            section = surfaces.get(surface)
            if section is None:
                continue
            merged = list(section.entries)
            merged.extend(entries)
            surfaces[surface] = SurfaceSection(
                surface=surface,
                source="wrapper" if not section.entries else section.source,
                entries=merged,
                cross_check=section.cross_check,
            )

    coverage = {
        name: _surface_coverage(section)
        for name, section in surfaces.items()
        if section.entries
    }
    service_section = surfaces.get("service")
    if service_section and any(entry.op == "dispatch" for entry in service_section.entries):
        coverage["service"] = "partial"
    return EffectsManifest(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        capture_sources=sources,
        surfaces={k: v for k, v in surfaces.items() if v.entries},
        coverage=coverage,
    )


def manifest_repo_paths(manifest: EffectsManifest | None) -> set[str]:
    if manifest is None:
        return set()
    section = manifest.surfaces.get("repo")
    if section is None:
        return set()
    paths: set[str] = set()
    for entry in section.entries:
        if entry.op not in _REPO_FILE_OPS:
            continue
        path = _normalize_repo_path(entry.target)
        if path:
            paths.add(path)
    return paths


def repo_change_set_from_manifest(manifest: EffectsManifest | None) -> ChangeSet | None:
    """Manifest op-intent projection — not authoritative for legacy files_* categories."""
    if manifest is None:
        return None
    section = manifest.surfaces.get("repo")
    if section is None:
        return ChangeSet(created=(), modified=(), deleted=())
    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    for entry in section.entries:
        path = _normalize_repo_path(entry.target)
        if not path:
            continue
        if entry.op == "write":
            created.append(path)
        elif entry.op == "edit":
            modified.append(path)
        elif entry.op == "delete":
            deleted.append(path)
    return ChangeSet(
        created=tuple(dict.fromkeys(created)),
        modified=tuple(dict.fromkeys(modified)),
        deleted=tuple(dict.fromkeys(deleted)),
    )


def manifest_digest(manifest: EffectsManifest) -> str:
    payload = manifest.model_dump(mode="json")
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def manifest_surface_counts(manifest: EffectsManifest) -> dict[str, int]:
    return {name: len(section.entries) for name, section in manifest.surfaces.items()}


def compact_manifest_for_body(
    manifest: EffectsManifest,
) -> EffectsManifest | dict[str, Any]:
    """Digest stub when the full manifest would exceed turn-body budget."""
    return {
        "schema_version": manifest.schema_version,
        "dispatch_id": manifest.dispatch_id,
        "thread_id": manifest.thread_id,
        "digest": manifest_digest(manifest),
        "surface_counts": manifest_surface_counts(manifest),
        "capture_sources": manifest.capture_sources,
        "external_effects": manifest.external_effects,
    }


def wrapper_effects_for_closeout(
    *,
    thread_id: str,
    dispatch_id: str,
    cortex_artifact_paths: list[str],
) -> dict[str, list[EffectEntry]]:
    effects: dict[str, list[EffectEntry]] = {
        "agent_bus": [
            EffectEntry(
                op="agent_bus.reply",
                target=thread_id,
                identity=f"{thread_id}#closeout",
            )
        ],
        "service": [
            EffectEntry(
                op="emit_implement_closeout_trigger",
                target=dispatch_id,
                identity=dispatch_id,
            )
        ],
    }
    if cortex_artifact_paths:
        effects["cortex"] = [
            EffectEntry(op="cortex.write", target=path, identity=path)
            for path in cortex_artifact_paths
        ]
    return effects


def merge_wrapper_manifest(
    *,
    dispatch_id: str,
    thread_id: str,
    base: EffectsManifest | None,
    cortex_artifact_paths: list[str],
) -> EffectsManifest:
    wrapper = build_effects_manifest(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        turns=(),
        capture_branch="B",
        wrapper_effects=wrapper_effects_for_closeout(
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            cortex_artifact_paths=cortex_artifact_paths,
        ),
    )
    if base is None:
        return wrapper
    merged_surfaces = dict(base.surfaces)
    for name, section in wrapper.surfaces.items():
        existing = merged_surfaces.get(name)
        if existing is None:
            merged_surfaces[name] = section
            continue
        merged_surfaces[name] = SurfaceSection(
            surface=name,
            source=existing.source,
            entries=[*existing.entries, *section.entries],
            cross_check=existing.cross_check,
        )
    sources = list(dict.fromkeys([*base.capture_sources, *wrapper.capture_sources]))
    return base.model_copy(update={"surfaces": merged_surfaces, "capture_sources": sources})


def resolve_repo_change_set(
    *,
    manifest: EffectsManifest | None,
    git_change_set: ChangeSet,
) -> ChangeSet:
    git_paths = (
        set(git_change_set.created)
        | set(git_change_set.modified)
        | set(git_change_set.deleted)
    )
    manifest_paths = manifest_repo_paths(manifest)
    extra = sorted(manifest_paths - git_paths)
    if not extra:
        return git_change_set
    return ChangeSet(
        created=git_change_set.created,
        modified=tuple(dict.fromkeys([*git_change_set.modified, *extra])),
        deleted=git_change_set.deleted,
    )


def verification_change_set(
    repo_change_set: ChangeSet, gate_d_created_rels: tuple[str, ...]
) -> ChangeSet:
    if not gate_d_created_rels:
        return repo_change_set
    return ChangeSet(
        created=repo_change_set.created + gate_d_created_rels,
        modified=repo_change_set.modified,
        deleted=repo_change_set.deleted,
    )


def serialize_effects_manifest_for_body(
    manifest: EffectsManifest | None,
    *,
    sidecar_appendix: list[str] | None = None,
) -> EffectsManifest | dict[str, Any] | None:
    if manifest is None:
        return None
    probe = json.dumps(
        {"effects_manifest": manifest.model_dump(mode="json")},
        separators=(",", ":"),
    )
    if len(probe) <= MAX_MANIFEST_BODY_PROBE:
        return manifest
    if sidecar_appendix is not None:
        sidecar_appendix.append(json.dumps(manifest.model_dump(mode="json"), indent=2))
    return compact_manifest_for_body(manifest)


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
    detail = _bounded_detail(args)
    if tool_type == _MCP_OP:
        tool_name = str(args.get("toolName") or "mcp")
        nested = args.get("args") if isinstance(args.get("args"), Mapping) else {}
        effective = _effective_mcp_args(nested)
        target = _mcp_target(tool_name, effective)
        identity = _mcp_identity(tool_name, effective)
        if tool_name == "dispatch":
            dispatched = _string_arg(effective, "tool")
            merged_detail = dict(detail or {})
            merged_detail["opaque_dispatch"] = True
            if dispatched:
                merged_detail["dispatched_tool"] = dispatched
                target = dispatched
            detail = merged_detail
        return EffectEntry(
            op=tool_name,
            target=target,
            detail=detail,
            identity=identity,
        )
    if tool_type in _REPO_FILE_OPS:
        path = _string_arg(args, "path", "filePath", "target")
        return EffectEntry(op=tool_type, target=path, detail=detail, identity=path)
    if tool_type == _REPO_SHELL_OP:
        command = _string_arg(args, "command")
        return EffectEntry(op="shell", target=command, detail=detail, identity=command)
    return EffectEntry(op=tool_type, target=_string_arg(args, "path"), detail=detail)


def _surface_for_tool_call(message: Mapping[str, Any], entry: EffectEntry) -> str | None:
    tool_type = str(message.get("type") or "")
    if tool_type in _REPO_FILE_OPS or tool_type == _REPO_SHELL_OP:
        return "repo"
    if tool_type != _MCP_OP:
        return None
    tool_name = entry.op
    if tool_name in _CORTEX_TOOLS:
        return "cortex"
    if tool_name in _AGENT_BUS_TOOLS:
        return "agent_bus"
    if tool_name in _FS_TOOLS:
        return "fs"
    if tool_name in _RAG_TOOLS:
        return "rag"
    if tool_name in _SERVICE_TOOLS:
        return "service"
    provider = str(
        (message.get("args") or {}).get("providerIdentifier")  # type: ignore[union-attr]
        if isinstance(message.get("args"), Mapping)
        else ""
    )
    if provider == _VORTEX_SERVER:
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
        surface = _surface_for_mcp_tool(tool_name)
        entry = EffectEntry(
            op=tool_name,
            target=str(payload.get("method") or payload.get("operation") or "") or None,
            detail=_bounded_detail(payload),
            identity=str(payload.get("correlation_id") or "") or None,
        )
        section = surfaces[surface]
        surfaces[surface] = SurfaceSection(
            surface=surface,
            source="mcp_events",
            entries=[*section.entries, entry],
            cross_check=section.cross_check,
        )


def _surface_for_mcp_tool(tool_name: str) -> str:
    if tool_name in _CORTEX_TOOLS:
        return "cortex"
    if tool_name in _AGENT_BUS_TOOLS:
        return "agent_bus"
    if tool_name in _FS_TOOLS:
        return "fs"
    if tool_name in _RAG_TOOLS:
        return "rag"
    return "service"


def _surface_coverage(section: SurfaceSection) -> str:
    if section.cross_check:
        return "partial"
    return "complete"


def _bounded_detail(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        text = json.dumps(dict(value), separators=(",", ":"))
    except (TypeError, ValueError):
        return {"raw": str(value)[:DetailCap]}
    if len(text) <= DetailCap:
        return dict(value)
    return {"truncated": text[:ResultCap]}


def _nested_tool_arguments(args: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = args.get("arguments")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, Mapping) else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
    if isinstance(raw, Mapping):
        return raw
    return {}


def _effective_mcp_args(nested: Mapping[str, Any]) -> Mapping[str, Any]:
    inner = _nested_tool_arguments(nested)
    if not inner:
        return nested
    merged: dict[str, Any] = dict(nested)
    merged.update(inner)
    return merged


def _mcp_target(tool_name: str, args: Mapping[str, Any]) -> str | None:
    if tool_name in _CORTEX_TOOLS:
        return _string_arg(args, "entity_id", "assertion_id", "id", "tool")
    if tool_name in _AGENT_BUS_TOOLS:
        thread = _string_arg(args, "new_slug", "slug", "thread_id", "thread")
        turn = _string_arg(args, "turn_number", "turn")
        if thread and turn:
            return f"{thread}#{turn}"
        return thread
    if tool_name in _FS_TOOLS:
        sandbox = _string_arg(args, "sandbox")
        path = _string_arg(args, "path")
        if sandbox and path:
            return f"{sandbox}:{path}"
        return path
    if tool_name in _RAG_TOOLS:
        return _string_arg(args, "op", "scope", "source_hash", "path")
    return _string_arg(args, "operation", "tool", "service")


def _mcp_identity(tool_name: str, args: Mapping[str, Any]) -> str | None:
    if tool_name in _CORTEX_TOOLS:
        return _string_arg(args, "entity_id", "assertion_id", "id")
    if tool_name in _FS_TOOLS:
        sandbox = _string_arg(args, "sandbox")
        path = _string_arg(args, "path")
        if sandbox and path:
            return f"{sandbox}:{path}"
        return path
    if tool_name in _RAG_TOOLS:
        return _string_arg(args, "source_hash", "op", "path")
    return _mcp_target(tool_name, args)


def _string_arg(args: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None


def _normalize_repo_path(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw.strip().lstrip("/")
