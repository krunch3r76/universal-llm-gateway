"""First-party multi-surface effects manifest for cursor-sdk closeout."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict

from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)

from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    canonicalize_capture_path,
    filter_manifest_swamp,
    is_swamp_excluded_path,
)
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)
from services.git_integration_worker.cursor_sdk_subagent_capture import (
    SUBAGENTS_SURFACE,
    ensure_subagents_surface,
    entry_from_subagent_message,
    is_subagent_tool_call,
)
from services.git_integration_worker.cursor_sdk_tool_result import (
    assertion_id_from_payload as _assertion_id_from_payload,
    unwrap_tool_result as _unwrap_tool_result,
)

CaptureBranch = Literal["A", "B", "NO_CAPTURE"]


class DroppedNonFileEntry(TypedDict):
    surface: str
    op: str
    target: str
    reason: str


DetailCap = 500
ResultCap = 2000
MAX_MANIFEST_BODY_PROBE = 4_000

_REPO_READ_OPS = frozenset({"observed"})
_REPO_WRITE_OPS = frozenset({"write", "edit", "delete"})
_REPO_FILE_OPS = _REPO_WRITE_OPS | _REPO_READ_OPS
_REPO_LABEL_OPS = _REPO_WRITE_OPS
_REPO_SHELL_OP = "shell"
_MCP_OP = "mcp"
_VORTEX_SERVER = "user-vortex"

_CORTEX_TOOLS = frozenset({"cortex", "cortex_brief"})
_CORTEX_WRITE_OPS = frozenset({"assert", "supersede", "observe", "friction"})
_ASSERTION_IDENTITY_RE = re.compile(r"^assertion:(\d+)$")
_AGENT_BUS_TOOLS = frozenset({"agent_bus", "agent_bus_read"})
_FS_TOOLS = frozenset({"fs"})
_FS_WRITE_OPS = frozenset(
    {
        "write",
        "append",
        "prepend",
        "insert_at_line",
        "replace",
        "md_replace",
        "md_append",
        "md_insert",
        "write_binary",
        "append_binary",
        "copy",
        "move",
    }
)
_RAG_TOOLS = frozenset({"rag"})
_SERVICE_TOOLS = frozenset(
    {
        "manage",
        "pipeline",
        "observability",
        "team_dispatch",
        "panel_dispatch",
        "retrieve",
        "tool_search",
        "dispatch",
    }
)
_PLUMBING_SURFACES = frozenset({"cortex", "agent_bus", "service"})
# Boundary surfaces preserved when git diff is empty — plumbing collapse must not
# erase nested/cortex-only capture (AC-9j).
_PRESERVE_ON_NO_CODE_CHANGE_SURFACES = frozenset(
    {"cortex", "fs", "agent_bus", "rag", SUBAGENTS_SURFACE}
)
_SURFACE_ORDER = ("repo", "cortex", "agent_bus", "fs", "rag", "service", "subagents")


def _git_change_set_empty(change_set: ChangeSet) -> bool:
    return not (change_set.created or change_set.modified or change_set.deleted)


def _manifest_declares_runtime_surface(base: EffectsManifest | None) -> bool:
    if base is None:
        return False
    if manifest_repo_write_paths(base):
        return True
    repo_section = base.surfaces.get("repo")
    if repo_section and any(entry.op == _REPO_SHELL_OP for entry in repo_section.entries):
        return True
    for name, section in base.surfaces.items():
        if name in _PLUMBING_SURFACES or name == "repo":
            continue
        if section.entries:
            return True
    return False


def is_genuinely_no_code_change(
    *,
    git_change_set: ChangeSet,
    base: EffectsManifest | None,
) -> bool:
    """True when baseline diff is empty and effects are cortex/bus/service plumbing only."""
    if not _git_change_set_empty(git_change_set):
        return False
    return not _manifest_declares_runtime_surface(base)


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
    if service_section and any(
        entry.op == "dispatch" for entry in service_section.entries
    ):
        coverage["service"] = "partial"
    manifest = EffectsManifest(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        capture_sources=sources,
        surfaces={k: v for k, v in surfaces.items() if v.entries},
        coverage=coverage,
    )
    return ensure_subagents_surface(manifest)


def manifest_repo_write_paths(
    manifest: EffectsManifest | None,
    *,
    source_repo: Path | None = None,
) -> set[str]:
    """Repo paths from write-family manifest ops — runtime surface and write evidence."""
    if manifest is None:
        return set()
    section = manifest.surfaces.get("repo")
    if section is None:
        return set()
    paths: set[str] = set()
    for entry in section.entries:
        if entry.op not in _REPO_WRITE_OPS:
            continue
        path = _normalize_repo_path(entry.target, repo_root=source_repo)
        if path:
            paths.add(path)
    return paths


def manifest_repo_paths(
    manifest: EffectsManifest | None,
    *,
    source_repo: Path | None = None,
) -> set[str]:
    """All repo file-op paths including read-family ``observed`` (dedup, branch-adjacent)."""
    if manifest is None:
        return set()
    section = manifest.surfaces.get("repo")
    if section is None:
        return set()
    paths: set[str] = set()
    for entry in section.entries:
        if entry.op not in _REPO_FILE_OPS:
            continue
        path = _normalize_repo_path(entry.target, repo_root=source_repo)
        if path:
            paths.add(path)
    return paths


def _dedupe_dropped_non_file_entries(
    entries: list[DroppedNonFileEntry],
) -> list[DroppedNonFileEntry]:
    seen: set[tuple[str, str, str, str]] = set()
    ordered: list[DroppedNonFileEntry] = []
    for entry in entries:
        key = (entry["surface"], entry["op"], entry["target"], entry["reason"])
        if key in seen:
            continue
        seen.add(key)
        ordered.append(entry)
    return ordered


def repo_change_set_from_manifest(
    manifest: EffectsManifest | None,
    *,
    source_repo: Path | None = None,
    mount_root: Path | None = None,
) -> tuple[ChangeSet | None, tuple[str, ...], list[DroppedNonFileEntry]]:
    """Manifest op-intent projection — cross-check input for closeout files_* categories.

    Returns ``(change_set, outside_repo_paths, dropped_non_file_entries)``.
    """
    if manifest is None:
        return None, (), []
    section = manifest.surfaces.get("repo")
    if section is None:
        return ChangeSet(created=(), modified=(), deleted=()), (), []
    mount = (
        (mount_root or resolve_mount_root(source_repo)).resolve()
        if source_repo
        else None
    )
    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    outside_repo: list[str] = []
    dropped: list[DroppedNonFileEntry] = []
    for entry in section.entries:
        classification = _classify_manifest_repo_entry(
            entry.target,
            source_repo=source_repo,
            mount_root=mount,
        )
        if classification is None:
            raw_target = str(entry.target or "").strip()
            if raw_target and raw_target != ".":
                dropped.append(
                    {
                        "surface": "repo",
                        "op": str(entry.op or ""),
                        "target": raw_target,
                        "reason": "non_file",
                    }
                )
            continue
        kind, path = classification
        if kind == "outside_repo":
            outside_repo.append(path)
            continue
        if entry.op not in _REPO_LABEL_OPS:
            continue
        if entry.op == "write":
            created.append(path)
        elif entry.op == "edit":
            modified.append(path)
        elif entry.op == "delete":
            deleted.append(path)
    return (
        ChangeSet(
            created=tuple(dict.fromkeys(created)),
            modified=tuple(dict.fromkeys(modified)),
            deleted=tuple(dict.fromkeys(deleted)),
        ),
        tuple(dict.fromkeys(outside_repo)),
        _dedupe_dropped_non_file_entries(dropped),
    )


def _classify_manifest_repo_entry(
    raw: str | None,
    *,
    source_repo: Path | None,
    mount_root: Path | None,
) -> tuple[Literal["repo", "outside_repo"], str] | None:
    """Return repo/outside_repo classification, or None when the entry is non-file."""
    if not raw or not str(raw).strip() or str(raw).strip() == ".":
        return None
    if source_repo is None:
        path = str(raw).strip().lstrip("/")
        return ("repo", path) if path and path != "." else None

    canon = canonicalize_capture_path(str(raw), source_repo=source_repo)
    if not canon.canonical_path and canon.scope == "external_or_unknown":
        if canon.canonicalization_reason == "empty_path":
            return None
        candidate = Path(str(raw).strip())
        if not candidate.is_absolute():
            return None
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if resolved.is_dir():
            return None
        if mount_root is not None:
            rel = mount_relative_path(mount_root, resolved)
            if rel is not None:
                return ("outside_repo", rel)
        return None

    path = canon.canonical_path
    if not path or path == ".":
        return None
    candidate = source_repo / path
    try:
        if candidate.exists() and candidate.is_dir():
            return None
    except OSError:
        return None
    if canon.scope == "external_or_unknown":
        if mount_root is None:
            return None
        try:
            resolved = (source_repo / path).resolve()
        except OSError:
            return None
        rel = mount_relative_path(mount_root, resolved)
        if rel is None:
            return None
        try:
            abs_path = (mount_root / rel).resolve()
            if abs_path.is_dir():
                return None
            if abs_path.is_file():
                return ("outside_repo", rel)
        except OSError:
            return None
        return None
    return ("repo", path)


def merge_repo_paths_into_manifest(
    manifest: EffectsManifest | None,
    paths: Iterable[str],
    *,
    source_repo: Path | None = None,
    source_label: str = "stream",
    op: str = "observed",
) -> EffectsManifest | None:
    """Append repo entries for paths not already present in the manifest."""
    normalized: list[str] = []
    existing = manifest_repo_paths(manifest, source_repo=source_repo)
    for raw in paths:
        path = _normalize_repo_path(raw, repo_root=source_repo)
        if not path or path in existing:
            continue
        normalized.append(path)
        existing.add(path)
    if not normalized:
        return manifest
    new_entries = [
        EffectEntry(op=op, target=path, identity=path) for path in normalized
    ]
    if manifest is None:
        return EffectsManifest(
            dispatch_id="",
            thread_id="",
            capture_sources=[source_label],
            surfaces={
                "repo": SurfaceSection(
                    surface="repo",
                    source=source_label,
                    entries=new_entries,
                )
            },
            coverage={"repo": "complete"},
        )
    repo_section = manifest.surfaces.get("repo")
    if repo_section is None:
        merged_surfaces = dict(manifest.surfaces)
        merged_surfaces["repo"] = SurfaceSection(
            surface="repo",
            source=source_label,
            entries=new_entries,
        )
    else:
        merged_surfaces = dict(manifest.surfaces)
        merged_surfaces["repo"] = SurfaceSection(
            surface="repo",
            source=repo_section.source,
            entries=[*repo_section.entries, *new_entries],
            cross_check=repo_section.cross_check,
        )
    sources = list(dict.fromkeys([*manifest.capture_sources, source_label]))
    coverage = dict(manifest.coverage)
    coverage["repo"] = coverage.get("repo", "complete")
    return manifest.model_copy(
        update={
            "surfaces": merged_surfaces,
            "capture_sources": sources,
            "coverage": coverage,
        }
    )


def merge_stream_tool_calls(
    manifest: EffectsManifest | None,
    tool_calls: tuple[ToolCallObservation, ...],
    *,
    source_repo: Path | None = None,
) -> EffectsManifest | None:
    """Fold stream-observed write paths missing from the conversation manifest."""
    paths = [tc.target_path for tc in tool_calls if tc.target_path]
    merged = merge_repo_paths_into_manifest(
        manifest,
        paths,
        source_repo=source_repo,
        source_label="stream",
    )
    return merge_stream_cortex_entries(merged, tool_calls)


def merge_stream_cortex_entries(
    manifest: EffectsManifest | None,
    tool_calls: tuple[ToolCallObservation, ...],
) -> EffectsManifest | None:
    """Fold stream-observed cortex write acks when conversation omitted results (AC-9j/18)."""
    from services.git_integration_worker.cursor_sdk_cortex_identity import (
        merge_stream_cortex_entries as _merge_stream_cortex_entries,
    )

    return _merge_stream_cortex_entries(manifest, tool_calls)


def _cortex_entry_from_stream_observation(
    obs: ToolCallObservation,
) -> EffectEntry | None:
    if obs.tool_name.lower() not in _CORTEX_TOOLS:
        return None
    raw_args = obs.args if isinstance(obs.args, Mapping) else {}
    nested = raw_args.get("args") if isinstance(raw_args.get("args"), Mapping) else raw_args
    effective = _effective_mcp_args(nested) if nested else {}
    op = _cortex_op_from_args(effective)
    if op not in _CORTEX_WRITE_OPS:
        return None
    detail = _bounded_detail(effective) if effective else None
    assertion_id = _cortex_result_assertion_id(obs.tool_name, effective, obs.result)
    identity = (
        f"assertion:{assertion_id}"
        if assertion_id is not None
        else _mcp_identity(obs.tool_name, effective)
    )
    target = _mcp_target(obs.tool_name, effective)
    return EffectEntry(
        op=obs.tool_name,
        target=target,
        detail=detail,
        identity=identity,
    )


def merge_artifact_paths(
    manifest: EffectsManifest | None,
    artifact_paths: Iterable[str],
    *,
    source_repo: Path | None = None,
) -> EffectsManifest | None:
    """Fold ``list_artifacts()`` paths into the repo surface when non-empty."""
    return merge_repo_paths_into_manifest(
        manifest,
        artifact_paths,
        source_repo=source_repo,
        source_label="artifacts",
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
    compact: dict[str, Any] = {
        "schema_version": manifest.schema_version,
        "dispatch_id": manifest.dispatch_id,
        "thread_id": manifest.thread_id,
        "digest": manifest_digest(manifest),
        "surface_counts": manifest_surface_counts(manifest),
        "capture_sources": manifest.capture_sources,
        "external_effects": manifest.external_effects,
    }
    if not manifest.surfaces:
        compact["surfaces"] = {}
    return compact


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
    git_change_set: ChangeSet | None = None,
) -> EffectsManifest:
    empty_git = ChangeSet(created=(), modified=(), deleted=())
    resolved_git = git_change_set if git_change_set is not None else empty_git
    if is_genuinely_no_code_change(git_change_set=resolved_git, base=base):
        sources = list(base.capture_sources) if base else []
        sources = list(dict.fromkeys([*sources, "wrapper"]))
        preserved: dict[str, SurfaceSection] = {}
        preserved_coverage: dict[str, str] = {}
        if base is not None:
            for name, section in base.surfaces.items():
                if name in _PRESERVE_ON_NO_CODE_CHANGE_SURFACES:
                    preserved[name] = section
                    if name in base.coverage:
                        preserved_coverage[name] = base.coverage[name]
        return EffectsManifest(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            capture_sources=sources,
            surfaces=preserved,
            coverage=preserved_coverage,
        )
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
    return base.model_copy(
        update={"surfaces": merged_surfaces, "capture_sources": sources}
    )


def resolve_repo_change_set(
    *,
    manifest: EffectsManifest | None,
    git_change_set: ChangeSet,
    source_repo: Path | None = None,
    mount_root: Path | None = None,
    baseline: dict[str, Any] | None = None,
    files_expected: list[str] | None = None,
    current_porcelain: dict[str, str] | None = None,
    admit_head: str | None = None,
    closeout_head: str | None = None,
) -> tuple[ChangeSet, tuple[str, ...], bool]:
    """Manifest-first change set — thin delegate to ``cursor_sdk_repo_precedence``."""
    from services.git_integration_worker.cursor_sdk_repo_precedence import (
        resolve_repo_change_set as _resolve_precedence,
    )

    change_set, extra_untracked, divergence, _ambient = _resolve_precedence(
        manifest=manifest,
        git_change_set=git_change_set,
        source_repo=source_repo,
        mount_root=mount_root,
        baseline=baseline,
        files_expected=files_expected,
        current_porcelain=current_porcelain,
        admit_head=admit_head,
        closeout_head=closeout_head,
    )
    return change_set, extra_untracked, divergence


def git_manifest_label_divergence(
    git_change_set: ChangeSet,
    manifest_change_set: ChangeSet,
) -> bool:
    """True when manifest op-intent labels disagree with git XY labels."""

    def _label(path: str, change_set: ChangeSet) -> str | None:
        if path in change_set.created:
            return "created"
        if path in change_set.modified:
            return "modified"
        if path in change_set.deleted:
            return "deleted"
        return None

    git_paths = (
        set(git_change_set.created)
        | set(git_change_set.modified)
        | set(git_change_set.deleted)
    )
    manifest_paths = (
        set(manifest_change_set.created)
        | set(manifest_change_set.modified)
        | set(manifest_change_set.deleted)
    )
    for path in git_paths | manifest_paths:
        git_label = _label(path, git_change_set)
        manifest_label = _label(path, manifest_change_set)
        if manifest_label is None:
            continue
        if git_label != manifest_label:
            return True
    return False


def _path_is_tracked(source_repo: Path, rel_path: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(source_repo), "ls-files", "--error-unmatch", rel_path],
            capture_output=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


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
    manifest_json = json.dumps(manifest.model_dump(mode="json"), indent=2)
    if sidecar_appendix is not None:
        sidecar_appendix.append(manifest_json)
    probe = json.dumps(
        {"effects_manifest": manifest.model_dump(mode="json")},
        separators=(",", ":"),
    )
    if len(probe) <= MAX_MANIFEST_BODY_PROBE:
        return manifest
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


def harvest_cortex_assertion_ids(manifest: EffectsManifest | None) -> list[str]:
    """Collect deduped assertion ids from cortex-surface manifest entry identities."""
    if manifest is None:
        return []
    section = manifest.surfaces.get("cortex")
    if section is None:
        return []
    ids: set[int] = set()
    for entry in section.entries:
        ident = entry.identity
        if not ident:
            continue
        match = _ASSERTION_IDENTITY_RE.match(ident)
        if match:
            ids.add(int(match.group(1)))
    return [str(i) for i in sorted(ids)]


def cortex_surface_has_write_op(manifest: EffectsManifest | None) -> bool:
    """True iff the cortex surface contains >=1 write-family op (assert/supersede/observe/friction).

    Used by the closeout builder to distinguish 'no cortex writes happened'
    (empty list is correct) from 'writes happened but no id was harvestable'
    (None + capture:cortex_writes_unattributed deviation). See
    todo:cursor-sdk-closeout-cortex-assertions-harvest.
    """
    if manifest is None:
        return False
    section = manifest.surfaces.get("cortex")
    if section is None:
        return False
    for entry in section.entries:
        detail = entry.detail or {}
        args = detail.get("args") if isinstance(detail, Mapping) else None
        op = _cortex_op_from_args(args) if isinstance(args, Mapping) else None
        if op in _CORTEX_WRITE_OPS:
            return True
    return False


def _cortex_op_from_args(args: Mapping[str, Any]) -> str | None:
    return _string_arg(args, "tool", "op")


def _cortex_result_assertion_id(
    tool_name: str,
    args: Mapping[str, Any],
    result: object,
) -> int | None:
    if tool_name not in _CORTEX_TOOLS:
        return None
    op = _cortex_op_from_args(args)
    if op not in _CORTEX_WRITE_OPS:
        return None
    try:
        payload = _unwrap_tool_result(result)
        if payload is None:
            return None
        return _assertion_id_from_payload(payload)
    except (TypeError, ValueError, AttributeError):
        return None


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
        result = message.get("result")
        assertion_id = _cortex_result_assertion_id(tool_name, effective, result)
        if assertion_id is not None:
            identity = f"assertion:{assertion_id}"
        if tool_name == "dispatch":
            dispatched = _string_arg(effective, "tool")
            merged_detail = dict(detail or {})
            merged_detail["opaque_dispatch"] = True
            if dispatched:
                merged_detail["dispatched_tool"] = dispatched
                target = dispatched
            detail = merged_detail
        elif tool_name in _FS_TOOLS:
            detail = _fs_compact_detail(effective)
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
    if is_subagent_tool_call(tool_type=tool_type):
        return entry_from_subagent_message(message)
    return EffectEntry(op=tool_type, target=_string_arg(args, "path"), detail=detail)


def _surface_for_tool_call(
    message: Mapping[str, Any], entry: EffectEntry
) -> str | None:
    tool_type = str(message.get("type") or "")
    if tool_type in _REPO_FILE_OPS or tool_type == _REPO_SHELL_OP:
        return "repo"
    if is_subagent_tool_call(tool_type=tool_type):
        return SUBAGENTS_SURFACE
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


def _fs_compact_detail(effective: Mapping[str, Any]) -> dict[str, Any] | None:
    detail: dict[str, Any] = {}
    op = _string_arg(effective, "op")
    sandbox = _string_arg(effective, "sandbox")
    path = _string_arg(effective, "path")
    if op:
        detail["op"] = op
    if sandbox:
        detail["sandbox"] = sandbox
    if path:
        detail["path"] = path
    return detail or None


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


def workspaces_mount_root() -> Path:
    """WORKSPACES_ROOT mount — mirrors MCP ``project_root()`` for repo enumeration."""
    return Path(os.environ.get("WORKSPACES_ROOT", "/mnt/torus/projects")).resolve()


def resolve_mount_root(source_repo: Path) -> Path:
    """Prefer configured WORKSPACES_ROOT when *source_repo* lives under it."""
    repo = source_repo.resolve()
    configured = workspaces_mount_root()
    try:
        repo.relative_to(configured)
        return configured
    except ValueError:
        if repo.name == "universal-llm-gateway":
            return repo.parent
        return repo


def registered_repo_roots(mount_root: Path | None = None) -> list[Path]:
    """Mirror ``_project_paths.repo_roots`` without importing mcp-server."""
    root = (mount_root or workspaces_mount_root()).resolve()
    if (root / ".git").exists():
        return [root]
    try:
        children = [child for child in sorted(root.iterdir()) if child.is_dir()]
    except FileNotFoundError:
        return [root]
    repos = [child for child in children if (child / ".git").exists()]
    if not repos:
        repos = [child for child in children if not child.name.startswith(".")]
    return [child.resolve() for child in (repos or [root])]


def mount_relative_path(mount_root: Path, path: Path) -> str | None:
    try:
        return str(path.resolve().relative_to(mount_root.resolve()))
    except ValueError:
        return None


def parse_fs_manifest_target(target: str | None) -> tuple[str, str] | None:
    if not target:
        return None
    from implement_admission.scheme_resolve import resolve_fs_ingress

    try:
        ingress = resolve_fs_ingress(target)
        return ingress.sandbox, ingress.rel_path.lstrip("/")
    except ValueError:
        pass
    if ":" not in target:
        return None
    sandbox, rel = target.split(":", 1)
    sandbox = sandbox.strip()
    rel = rel.strip().lstrip("/")
    if sandbox and rel:
        return sandbox, rel
    return None


def resolve_fs_target_absolute(
    target: str | None,
    *,
    mount_root: Path,
    cortex_root: Path,
) -> Path | None:
    if not target:
        return None
    parsed = parse_fs_manifest_target(target)
    if parsed is not None:
        sandbox, rel = parsed
        if sandbox == "workspaces":
            return (mount_root / rel).resolve()
        if sandbox == "cortex":
            return (cortex_root / rel).resolve()
        return (mount_root / rel).resolve()
    return (mount_root / target.lstrip("/")).resolve()


def classify_mount_path(
    path: Path,
    *,
    source_repo: Path,
    mount_root: Path,
    repo_roots: list[Path] | None = None,
) -> Literal[
    "source_repo", "other_repo", "shared_cursor", "unknown_root_child", "outside_mount"
]:
    resolved = path.resolve()
    rel = mount_relative_path(mount_root, resolved)
    if rel is None:
        return "outside_mount"
    if rel == ".cursor" or rel.startswith(".cursor/"):
        return "shared_cursor"
    roots = repo_roots or registered_repo_roots(mount_root)
    source_resolved = source_repo.resolve()
    for repo in roots:
        try:
            resolved.relative_to(repo.resolve())
        except ValueError:
            continue
        if repo.resolve() == source_resolved:
            return "source_repo"
        return "other_repo"
    return "unknown_root_child"


def manifest_fs_targets(manifest: EffectsManifest | None) -> list[str]:
    if manifest is None:
        return []
    section = manifest.surfaces.get("fs")
    if section is None:
        return []
    targets: list[str] = []
    for entry in section.entries:
        target = entry.target or entry.identity
        if target:
            targets.append(target)
    return targets


def _fs_entry_write_op(entry: EffectEntry) -> str | None:
    detail = entry.detail if isinstance(entry.detail, Mapping) else None
    if detail:
        op = detail.get("op")
        if isinstance(op, str) and op in _FS_WRITE_OPS:
            return op
    return None


def _fs_entry_path(entry: EffectEntry) -> str | None:
    detail = entry.detail if isinstance(entry.detail, Mapping) else None
    if detail:
        path = detail.get("path")
        if isinstance(path, str) and path.strip():
            return path.strip()
    target = entry.target or entry.identity
    return target.strip() if isinstance(target, str) and target.strip() else None


def _fs_entry_sandbox(entry: EffectEntry) -> str | None:
    detail = entry.detail if isinstance(entry.detail, Mapping) else None
    if not detail:
        return None
    sandbox = detail.get("sandbox")
    return sandbox.strip() if isinstance(sandbox, str) and sandbox.strip() else None


def manifest_fs_write_targets(
    manifest: EffectsManifest | None,
) -> list[tuple[str | None, str]]:
    if manifest is None:
        return []
    section = manifest.surfaces.get("fs")
    if section is None:
        return []
    targets: list[tuple[str | None, str]] = []
    for entry in section.entries:
        if _fs_entry_write_op(entry) is None:
            continue
        path = _fs_entry_path(entry)
        if not path:
            continue
        targets.append((_fs_entry_sandbox(entry), path))
    return targets


def _normalize_offgit_uri(sandbox: str | None, path: str) -> str:
    raw = path.strip()
    lower = raw.lower()
    if lower.startswith("cortex://"):
        return raw
    if lower.startswith("workspaces://"):
        return raw
    if lower.startswith("cortex:"):
        return f"cortex://{raw.split(':', 1)[1].lstrip('/')}"
    sandbox_key = (sandbox or "").strip().lower()
    if sandbox_key == "cortex":
        return f"cortex://{raw.lstrip('/')}"
    if sandbox_key == "workspaces":
        return f"workspaces://{raw.lstrip('/')}"
    if ":" in raw and not lower.startswith(("cortex", "workspaces")):
        prefix, _, rest = raw.partition(":")
        if prefix.lower() in {"cortex", "workspaces"} and rest:
            scheme = prefix.lower()
            return f"{scheme}://{rest.lstrip('/')}"
    return f"workspaces://{raw.lstrip('/')}"


def _is_excluded_offgit_uri(uri: str, *, sidecar_ref: str) -> bool:
    if uri == sidecar_ref:
        return True
    lower = uri.lower()
    if "tmp/reviews/closeouts/" in lower:
        return True
    return False


def normalize_expected_cortex_deliverable_uri(raw: str) -> str | None:
    """Return canonical ``cortex://`` URI when *raw* is cortex-shaped; else None.

    Repo-relative paths are never inferred as cortex URIs (OOB policy).
    """
    stripped = raw.strip()
    if not stripped:
        return None
    lower = stripped.lower()
    if lower.startswith("cortex://"):
        return f"cortex://{stripped[9:].lstrip('/')}"
    if lower.startswith("cortex:"):
        return f"cortex://{stripped.split(':', 1)[1].lstrip('/')}"
    return None


def collect_expected_cortex_deliverable_uris(
    *,
    light_bounded_expected_paths: tuple[str, ...] = (),
    files_expected: list[str] | None = None,
    cortex_artifact_paths: list[str] | None = None,
) -> list[str]:
    """Deduped cortex:// deliverables from pinned, light-bounded, and files_expected."""
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in (
        *(cortex_artifact_paths or []),
        *light_bounded_expected_paths,
        *(files_expected or []),
    ):
        uri = normalize_expected_cortex_deliverable_uri(raw)
        if uri and uri not in seen:
            seen.add(uri)
            ordered.append(uri)
    return ordered


def oob_cortex_write_findings(
    *,
    expected_cortex_uris: list[str],
    offgit_uris: list[str],
    cortex_root: Path,
) -> tuple[list[str], str | None]:
    """OOB deviations when a landed cortex deliverable is absent from fs write-capture."""
    offgit_set = set(offgit_uris)
    deviations: list[str] = []
    for uri in expected_cortex_uris:
        if uri in offgit_set:
            continue
        rel = uri[9:].lstrip("/") if uri.lower().startswith("cortex://") else uri
        if not (cortex_root / rel).exists():
            continue
        deviations.append(f"capture:oob_cortex_write_unobserved:{uri}")
    divergence_reason = "capture:oob_cortex_write_unobserved" if deviations else None
    return deviations, divergence_reason


def manifest_offgit_deliverable_uris(
    manifest: EffectsManifest | None,
    *,
    sidecar_ref: str,
) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for sandbox, path in manifest_fs_write_targets(manifest):
        uri = _normalize_offgit_uri(sandbox, path)
        if _is_excluded_offgit_uri(uri, sidecar_ref=sidecar_ref):
            continue
        if uri not in seen:
            seen.add(uri)
            ordered.append(uri)
    return ordered


def snapshot_outside_repo_paths(
    mount_root: Path,
    repo_roots: list[Path] | None = None,
) -> frozenset[str]:
    """Workspaces-relative paths under *mount_root* but outside every registered repo."""
    roots = repo_roots or registered_repo_roots(mount_root)
    roots_resolved = {repo.resolve() for repo in roots}

    def _under_repo(candidate: Path) -> bool:
        resolved = candidate.resolve()
        for repo in roots_resolved:
            try:
                resolved.relative_to(repo)
                return True
            except ValueError:
                continue
        return False

    found: set[str] = set()
    mount_resolved = mount_root.resolve()
    if not mount_resolved.is_dir():
        return frozenset()
    for path in mount_resolved.rglob("*"):
        if not path.is_file() or _under_repo(path):
            continue
        rel = mount_relative_path(mount_resolved, path)
        if rel is not None and not is_swamp_excluded_path(rel):
            found.add(rel)
    return frozenset(filter_manifest_swamp(found))


def _normalize_repo_path(
    raw: str | None,
    repo_root: Path | str | None = None,
) -> str | None:
    if not raw:
        return None
    if repo_root is None:
        return raw.strip().lstrip("/")
    from services.git_integration_worker.cursor_sdk_capture_status import (
        canonicalize_capture_path,
    )

    canon = canonicalize_capture_path(raw, source_repo=Path(repo_root))
    return canon.canonical_path or None
