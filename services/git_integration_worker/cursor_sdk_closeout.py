"""Closeout validation and bounded delivery helpers for cursor-sdk worker dispatches."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.closeout_models import (
    EffectsManifest,
    EvidenceUris,
    ImplementCloseout,
    Verification,
)
from implement_admission.deliverable_verification import (
    evaluate_deliverable_verification,
)
from implement_admission.normalize import _files_from_packet
from implement_admission.spec import CloseoutStatus, ImplementSpec
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.episode_residue import residue_actions
from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    attribution_effects_paths,
    baseline_dirty_in_expected,
    degrade_status_for_capture,
    filter_manifest_swamp,
    gitignored_manifest_paths,
    normalize_wt_baseline,
    partition_gitignored_from_change_set,
    resolve_closeout_capture_fields,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    artifact_paths_for_closeout,
    cortex_expected_rels,
    full_result_text,
    relocate_oversize_closeout_body_async,
    relocate_oversize_closeout_body_sync,
    resolve_cortex_pinned_deliverables,
    sidecar_workspaces_ref,
    write_repo_sidecar,
)
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_closeout_relocated,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    CaptureBranch,
    collect_expected_cortex_deliverable_uris,
    compact_manifest_for_body,
    cortex_surface_has_write_op,
    harvest_cortex_assertion_ids,
    manifest_offgit_deliverable_uris,
    merge_wrapper_manifest,
    no_capture_degraded_reason,
    oob_cortex_write_findings,
    registered_repo_roots,
    repo_change_set_from_manifest,
    resolve_mount_root,
    resolve_repo_change_set,
    serialize_effects_manifest_for_body,
    snapshot_outside_repo_paths,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    verification_change_set as build_verification_change_set,
)
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)

logger = get_logger(__name__)

# Must stay aligned with ``libs/agent_bus_store/turns_models`` bus invariants.
MAX_TURN_BODY_CHARS = 8_000
_CLOSEOUT_FILE_HEAD = 5
_POST_WAIT_POLL_ATTEMPTS = 3
_POST_WAIT_POLL_INTERVAL_S = 0.2


@dataclass(frozen=True)
class SdkRunOutcome:
    body: str
    status: str
    duration_ms: int
    tool_call_count: int
    effects_manifest: EffectsManifest | None = None
    capture_branch: CaptureBranch | None = None
    # Per-call detail from the live stream (friction 21654) — the channel that
    # can see a tool call the runtime truncates/rejects before it reaches
    # run.conversation(). Populated by observe_run_stream in the drive path.
    tool_calls: tuple[ToolCallObservation, ...] = ()
    # Normalized from TurnEndedUpdate.usage (+ TokenDelta fallthrough when absent).
    usage: dict[str, Any] | None = None
    usage_capture_status: str = "missing"
    sdk_request_id: str | None = None
    request_id_source: str | None = None
    # Local-bridge join keys when platform requestId is not on the wire (0.1.9:
    # request_id lives on SDKRequestMessage / CursorSDKError only — not RunResult).
    sdk_run_id: str | None = None
    sdk_agent_id: str | None = None
    degraded_reasons: tuple[str, ...] = ()
    sdk_git: dict[str, Any] | None = None
    stream_only_deviations: tuple[str, ...] = ()


@dataclass(frozen=True)
class PostWaitSnapshot:
    conversation: list[Any]
    artifact_paths: tuple[str, ...]
    sdk_git: dict[str, Any] | None


@dataclass(frozen=True)
class CloseoutDelivery:
    body: str
    sidecar_ref: str
    sidecar_path: Path
    full_result_bytes: int
    closeout_status: CloseoutStatus


def _parse_porcelain_z(raw: bytes) -> dict[str, str]:
    entries: dict[str, str] = {}
    if not raw:
        return entries
    parts = raw.split(b"\0")
    i = 0
    while i < len(parts):
        chunk = parts[i]
        if not chunk:
            i += 1
            continue
        text = chunk.decode("utf-8", errors="replace")
        if len(text) >= 4 and text[2] == " ":
            status = text[:2]
            path = text[3:]
            if status.startswith("R") and i + 1 < len(parts):
                path = parts[i + 1].decode("utf-8", errors="replace")
                i += 1
            entries[path] = status
        i += 1
    return entries


def _hash_worktree_file(source_repo: Path, path: str) -> str | None:
    try:
        data = (source_repo / path).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def capture_wt_baseline(source_repo: Path) -> dict[str, str] | None:
    """Snapshot working-tree paths at admit for later delta isolation."""
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(source_repo),
                "status",
                "--porcelain",
                "-z",
                "--untracked-files=all",
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("wt baseline capture failed for repo=%s: %s", source_repo, exc)
        return None
    return _parse_porcelain_z(proc.stdout)


def capture_wt_baseline_with_hashes(source_repo: Path) -> dict[str, Any] | None:
    """Porcelain codes plus content hashes and outside-repo census at admit."""
    codes = capture_wt_baseline(source_repo)
    if codes is None:
        return None
    hashes: dict[str, str] = {}
    for path in codes:
        digest = _hash_worktree_file(source_repo, path)
        if digest is not None:
            hashes[path] = digest
    mount = resolve_mount_root(source_repo)
    outside = sorted(snapshot_outside_repo_paths(mount, registered_repo_roots(mount)))
    return {"codes": codes, "hashes": hashes, "outside_repo": outside}


def run_touched_files_lint(
    source_repo: Path,
    change_set: ChangeSet,
) -> tuple[Verification, str | None]:
    """Run ``ruff check`` on touched ``*.py`` paths from the git change set."""
    py_paths = [
        path
        for path in (*change_set.created, *change_set.modified)
        if path.endswith(".py")
    ]
    if not py_paths:
        return (
            Verification(command="ruff check (no python files touched)", exit_code=0),
            None,
        )
    abs_paths = [str(source_repo / path) for path in py_paths]
    command = f"ruff check {len(py_paths)} touched files"
    try:
        proc = subprocess.run(
            ["ruff", "check", *abs_paths],
            capture_output=True,
            timeout=60,
        )
    except FileNotFoundError:
        return Verification(
            command=command, exit_code=0
        ), "verification:lint_unavailable"
    except subprocess.TimeoutExpired:
        return Verification(
            command=command, exit_code=0
        ), "verification:lint_unavailable"
    return Verification(command=command, exit_code=proc.returncode), None


def _split_baseline(
    baseline: dict[str, Any] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    return normalize_wt_baseline(baseline)


def changed_paths(source_repo: Path, baseline: dict[str, Any] | None) -> ChangeSet:
    """Derive created/modified/deleted paths vs an admit-time baseline."""
    current = capture_wt_baseline(source_repo)
    if current is None:
        return ChangeSet(created=(), modified=(), deleted=())
    codes, hashes = _split_baseline(baseline)
    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    all_paths = set(current) | set(codes)
    for path in sorted(all_paths):
        cur = current.get(path)
        prev = codes.get(path)
        if cur is None and prev is not None:
            deleted.append(path)
        elif cur is not None and prev is None:
            if cur.startswith("?"):
                created.append(path)
            else:
                modified.append(path)
        elif cur is not None and prev is not None and cur != prev:
            modified.append(path)
        elif cur is not None and prev is not None and cur == prev and path in hashes:
            current_hash = _hash_worktree_file(source_repo, path)
            if current_hash is not None and current_hash != hashes[path]:
                if prev.startswith("?"):
                    created.append(path)
                else:
                    modified.append(path)
    return ChangeSet(
        created=tuple(created),
        modified=tuple(modified),
        deleted=tuple(deleted),
    )


def _baseline_outside_repo_paths(baseline: dict[str, Any] | None) -> frozenset[str]:
    if baseline is None:
        return frozenset()
    raw = baseline.get("outside_repo")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(path) for path in raw)


def reconcile_workspace_changes(
    *,
    source_repo: Path,
    baseline: dict[str, Any] | None,
    manifest: EffectsManifest | None = None,
    mount_root: Path | None = None,
) -> tuple[ChangeSet, tuple[str, ...], tuple[str, ...]]:
    """Multi-root disk reconciliation: git diff + outside-repo paths + gitignored."""
    from services.git_integration_worker.cursor_sdk_manifest import resolve_mount_root

    mount = (mount_root or resolve_mount_root(source_repo)).resolve()
    repos = registered_repo_roots(mount)
    git_change = (
        ChangeSet(created=(), modified=(), deleted=())
        if baseline is None
        else changed_paths(source_repo, baseline)
    )
    git_changed = (
        set(git_change.created) | set(git_change.modified) | set(git_change.deleted)
    )
    gitignored = gitignored_manifest_paths(
        manifest,
        source_repo=source_repo,
        git_changed=git_changed,
    )
    baseline_outside = _baseline_outside_repo_paths(baseline)
    current_outside = snapshot_outside_repo_paths(mount, repos)
    new_outside = tuple(sorted(current_outside - baseline_outside))
    return git_change, gitignored, new_outside


def _baseline_dirty_in_expected(
    baseline: dict[str, Any] | None, files_expected: list[str]
) -> bool:
    return baseline_dirty_in_expected(baseline, files_expected)


def _files_expected_from_packet(packet_text: str | None) -> list[str]:
    if not packet_text:
        return []
    return _files_from_packet(packet_text)


def _files_expected_for_pinning(
    packet_text: str | None,
    deliverables_expected: bool,
    light_bounded_expected_paths: tuple[str, ...],
) -> list[str]:
    if light_bounded_expected_paths:
        return list(light_bounded_expected_paths)
    if deliverables_expected:
        return _files_expected_from_packet(packet_text)
    return []


def verify_deliverables(
    *,
    spec: ImplementSpec | None,
    change_set: ChangeSet,
    outcome: SdkRunOutcome,
    sidecar_path: Path | None,
    files_expected: list[str] | None = None,
    baseline: dict[str, Any] | None = None,
    source_repo: Path | None = None,
) -> list[Verification]:
    """Gate-D probe; ``source_repo`` enables on-disk backstop for uncaptured repo paths.

    See ``classify_capture_status`` for the repo capture trust boundary (porcelain +
    manifest fold vs shell side effects).
    """
    expected = files_expected or (spec.scope.files_expected if spec else [])
    closeout_probe = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="probe",
        source_ref="todo:probe",
        files_created=list(change_set.created),
        files_modified=list(change_set.modified),
        files_deleted=list(change_set.deleted),
    )
    sidecar_ok = sidecar_path is not None and sidecar_path.is_file()
    return evaluate_deliverable_verification(
        spec=spec,
        closeout=closeout_probe,
        sidecar_resolvable=sidecar_ok,
        run_finished=outcome.status == "finished",
        tool_call_count=outcome.tool_call_count,
        baseline_dirty_in_expected=_baseline_dirty_in_expected(baseline, expected),
        files_expected=expected,
        source_repo=source_repo,
    )


def count_tool_calls(turns: list) -> int:
    total = 0
    for turn in turns:
        steps = getattr(getattr(turn, "turn", None), "steps", ()) or ()
        total += sum(1 for step in steps if getattr(step, "type", "") == "toolCall")
    return total


def merge_degraded_reasons(
    singular: str | None,
    *extra: str,
) -> tuple[str, ...]:
    """Dual-emit compat: singular reason first, then additive extras."""
    reasons: list[str] = []
    if singular:
        reasons.append(singular)
    for reason in extra:
        if reason and reason not in reasons:
            reasons.append(reason)
    return tuple(reasons)


def degraded_reasons_from_exception(exc: BaseException) -> tuple[str, ...]:
    """Map SDK/bridge failures to ``degraded_reasons[]`` tokens (A sidecar §A4)."""
    from cursor_sdk.errors import (
        AgentBusyError,
        APITimeoutError,
        AuthenticationError,
        CursorSDKError,
        NotFoundError,
        RateLimitError,
    )

    from services.git_integration_worker.cursor_home import (
        CursorHomeConfigError,
        CursorVenvConfigError,
    )

    if isinstance(exc, RateLimitError):
        return ("sdk_rate_limited",)
    if isinstance(exc, AgentBusyError):
        return ("sdk_agent_busy",)
    if isinstance(exc, AuthenticationError):
        return ("sdk_auth_failed",)
    if isinstance(exc, NotFoundError):
        return ("sdk_run_not_found",)
    if isinstance(exc, APITimeoutError):
        return ("sdk_timeout",)
    if isinstance(exc, CursorSDKError):
        code = getattr(exc, "code", None) or "unknown"
        return (f"sdk_error:{code}",)
    if type(exc).__name__ == "SdkRunAbortedError":
        return ("bridge_read_timeout",)
    if isinstance(exc, CursorHomeConfigError):
        return ("bridge_env_config",)
    if isinstance(exc, CursorVenvConfigError):
        return ("bridge_env_config",)
    return ("worker_dispatch_failed",)


def extract_sdk_git_snapshot(git_info: Any) -> dict[str, Any] | None:
    if git_info is None:
        return None
    branches = getattr(git_info, "branches", None) or ()
    if not branches:
        return None
    first = branches[0]
    return {
        "repo_url": getattr(first, "repo_url", None),
        "branch": getattr(first, "branch", None),
        "pr_url": getattr(first, "pr_url", None),
    }


def _git_branch_name(source_repo: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(source_repo), "branch", "--show-current"],
            capture_output=True,
            check=True,
            timeout=5,
            text=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    branch = proc.stdout.strip()
    return branch or None


def sdk_fs_git_mismatch_reason(
    sdk_git: dict[str, Any] | None,
    source_repo: Path,
) -> str | None:
    """Return ``sdk_fs_mismatch`` when SDK git disagrees with FS/git (day-1 XCHECK)."""
    if not sdk_git:
        return None
    fs_branch = _git_branch_name(source_repo)
    sdk_branch = sdk_git.get("branch")
    if fs_branch and sdk_branch and fs_branch != sdk_branch:
        return "sdk_fs_mismatch"
    return None


def _post_wait_needs_poll(*, conversation: list[Any], status: str) -> bool:
    if status == "finished" and not conversation:
        return True
    return False


def read_post_wait_snapshot(
    *,
    run: Any,
    agent: Any,
    result: Any,
    poll_fallback: bool = True,
) -> PostWaitSnapshot:
    """Post-wait authority reads; bounded poll when immediate snapshot is incomplete."""

    def _read() -> tuple[list[Any], tuple[str, ...], dict[str, Any] | None]:
        turns = run.conversation()
        artifact_paths: list[str] = []
        list_artifacts_fn = getattr(agent, "list_artifacts", None)
        if callable(list_artifacts_fn):
            try:
                raw_artifacts = list_artifacts_fn()
                if raw_artifacts:
                    artifact_paths = [str(path) for path in raw_artifacts if path]
            except Exception:  # noqa: BLE001
                artifact_paths = []
        sdk_git = extract_sdk_git_snapshot(getattr(result, "git", None))
        return turns, tuple(artifact_paths), sdk_git

    status = str(getattr(result, "status", ""))
    conversation, artifact_paths, sdk_git = _read()
    if poll_fallback and _post_wait_needs_poll(
        conversation=conversation, status=status
    ):
        import time

        for _ in range(_POST_WAIT_POLL_ATTEMPTS):
            time.sleep(_POST_WAIT_POLL_INTERVAL_S)
            conversation, artifact_paths, sdk_git = _read()
            if not _post_wait_needs_poll(conversation=conversation, status=status):
                break
    return PostWaitSnapshot(
        conversation=conversation,
        artifact_paths=artifact_paths,
        sdk_git=sdk_git,
    )


def stream_only_effect_deviations(
    *,
    stream_tool_calls: tuple[ToolCallObservation, ...],
    conversation_tool_call_count: int,
) -> tuple[str, ...]:
    if stream_tool_calls and conversation_tool_call_count < len(stream_tool_calls):
        return ("stream_only_effect",)
    return ()


def degraded_implement_reason(outcome: SdkRunOutcome) -> str | None:
    """Return a machine reason when an implement closeout must not claim success."""
    if outcome.status != "finished":
        return f"run_status={outcome.status}"
    if outcome.tool_call_count == 0:
        return "zero_tool_calls"
    if outcome.capture_branch:
        no_capture = no_capture_degraded_reason(outcome.capture_branch)
        if no_capture:
            return no_capture
    return None


def empty_output_degraded_reason(outcome: SdkRunOutcome) -> str | None:
    """Contract-independent invariant guard for friction 19819.

    A finished run whose captured body (after transcript reconstruction by
    ``resolve_run_body``) is empty must never report ``status: complete`` with a
    0-byte sidecar. Returns ``"empty_terminal_output"`` (mapped to PARTIAL by
    ``_map_closeout_status``) so the silent-success failure mode is surfaced
    explicitly. Non-finished runs are already covered by the ``run_status=`` reason.
    """
    if outcome.status == "finished" and not outcome.body.strip():
        return "empty_terminal_output"
    return None


def empty_assistant_turn_reason(outcome: SdkRunOutcome) -> str | None:
    """Hollow-model-no-op guard for friction 24299 — contract- and status-independent.

    A run whose captured body is empty AND which made zero tool calls produced
    nothing: an empty assistant turn (``content: []``). This is a run-health
    failure that must outrank downstream deliverable-completeness reasons
    (``pinned_deliverable_*``), otherwise a secondary pin-write miss becomes the
    primary ``degraded_reason`` operators see and the model no-op is misdiagnosed.

    Distinct from ``empty_output_degraded_reason`` (finished-gated, body-only, so
    it misses a non-``finished`` empty stop) and from ``degraded_implement_reason``'s
    ``zero_tool_calls`` (implement-only). This fires for every contract and every
    status, closing the hole that let a light-bounded/consult hollow no-op reach
    the pin path with ``degraded_reason=None``.
    """
    if not outcome.body.strip() and outcome.tool_call_count == 0:
        return "empty_assistant_turn"
    return None


def _map_closeout_status(degraded_reason: str | None) -> CloseoutStatus:
    """Map the worker's degraded_reason to an ImplementCloseout status.

    None              -> COMPLETE (clean finished run with tool calls)
    "run_status=..."  -> FAILED   (the SDK run itself did not finish)
    anything else     -> PARTIAL  (ran but degraded, e.g. "zero_tool_calls")
    """
    if degraded_reason is None:
        return CloseoutStatus.COMPLETE
    if degraded_reason.startswith("run_status="):
        return CloseoutStatus.FAILED
    return CloseoutStatus.PARTIAL


def finalize_closeout_body(
    body: str,
    *,
    body_relocated: dict[str, Any] | None = None,
) -> str:
    """Deterministically shrink an oversize closeout JSON body to the bus limit."""
    if len(body) <= MAX_TURN_BODY_CHARS:
        return body

    payload = json.loads(body)
    reduced: dict[str, Any] = {
        "schema_version": payload.get("schema_version", 1),
        "status": payload["status"],
        "summary": payload["summary"],
        "source_ref": payload["source_ref"],
    }
    if payload.get("capture_status") is not None:
        reduced["capture_status"] = payload["capture_status"]
    if payload.get("evidence_uris"):
        reduced["evidence_uris"] = payload["evidence_uris"]
    verification = payload.get("verification") or []
    failed_verification = [
        item
        for item in verification
        if isinstance(item, dict) and item.get("exit_code")
    ]
    if failed_verification:
        reduced["verification"] = failed_verification
    effects = payload.get("effects")
    if effects is not None:
        reduced["effects_total"] = len(effects)
        reduced["effects"] = list(effects[:_CLOSEOUT_FILE_HEAD])
    for field, total_field in (
        ("files_created", "files_created_total"),
        ("files_modified", "files_modified_total"),
        ("files_deleted", "files_deleted_total"),
        ("files_outside_repo", "files_outside_repo_total"),
    ):
        files = payload.get(field) or []
        reduced[total_field] = len(files)
        reduced[field] = list(files[:_CLOSEOUT_FILE_HEAD])
    ignored = payload.get("files_untracked_or_ignored") or []
    if ignored:
        reduced["files_untracked_or_ignored_total"] = len(ignored)
        reduced["files_untracked_or_ignored"] = list(ignored[:_CLOSEOUT_FILE_HEAD])
    offgit = payload.get("files_offgit_produced") or []
    if offgit:
        reduced["files_offgit_produced_total"] = len(offgit)
        reduced["files_offgit_produced"] = list(offgit[:_CLOSEOUT_FILE_HEAD])
    dropped = payload.get("dropped_non_file_entries") or []
    if dropped:
        reduced["dropped_non_file_entries_total"] = len(dropped)
        reduced["dropped_non_file_entries"] = list(dropped[:_CLOSEOUT_FILE_HEAD])
    if payload.get("deviations"):
        reduced["deviations"] = list(payload["deviations"][:_CLOSEOUT_FILE_HEAD])
    residue = payload.get("propagation_residue") or []
    if residue:
        reduced["propagation_residue"] = list(residue[:_CLOSEOUT_FILE_HEAD])
    if body_relocated is not None:
        reduced["body_relocated"] = body_relocated

    result = json.dumps(reduced, separators=(",", ":"))
    if len(result) <= MAX_TURN_BODY_CHARS:
        return result

    summary = str(reduced["summary"])
    overhead = len(result) - len(summary)
    max_summary = max(40, MAX_TURN_BODY_CHARS - overhead - 3)
    reduced["summary"] = summary[:max_summary] + "..."
    result = json.dumps(reduced, separators=(",", ":"))
    if len(result) <= MAX_TURN_BODY_CHARS:
        return result

    minimal: dict[str, Any] = {
        "schema_version": 1,
        "status": payload["status"],
        "summary": str(payload["summary"])[:200],
        "evidence_uris": payload.get("evidence_uris"),
    }
    if residue:
        minimal["propagation_residue"] = list(residue[:_CLOSEOUT_FILE_HEAD])
    if body_relocated is not None:
        minimal["body_relocated"] = body_relocated
    result = json.dumps(minimal, separators=(",", ":"))
    while len(result) > MAX_TURN_BODY_CHARS and len(minimal["summary"]) > 20:
        minimal["summary"] = str(minimal["summary"])[:-10]
        result = json.dumps(minimal, separators=(",", ":"))
    return result[:MAX_TURN_BODY_CHARS] if len(result) > MAX_TURN_BODY_CHARS else result


def build_implement_closeout_body(
    *,
    dispatch_id: str,
    outcome: SdkRunOutcome,
    degraded_reason: str | None,
    sidecar_ref: str,
    result_bytes: int,
    thread_id: str,
    work_item_ref: str | None,
    change_set: ChangeSet | None = None,
    verification: list[Verification] | None = None,
    cortex_artifact_paths: list[str] | None = None,
    capture_status: str | None = None,
    divergence_reason: str | None = None,
    deviations: list[str] | None = None,
    effects_manifest: EffectsManifest | None = None,
    sidecar_appendix: list[str] | None = None,
    cortex_first: bool = False,
    files_untracked_or_ignored: list[str] | None = None,
    files_outside_repo: list[str] | None = None,
    offgit_deliverable_uris: list[str] | None = None,
    dropped_non_file_entries: list[dict[str, str]] | None = None,
) -> str:
    """Build a compact, valid ImplementCloseout JSON turn body.

    The full Composer result lives in the sidecar; this body carries only the
    minimal closeout fields Stargate's trigger_closeout_from_turn requires plus a
    one-line metadata summary. Always small (< 500 chars).

    ``source_ref`` is the canonical work-item ref (the lifecycle target); the
    sidecar is evidence, not identity, so it lands in ``evidence_uris``. When no
    work-item ref exists (message dispatch) ``sidecar_ref`` is the required-field
    fallback — Stargate /closeout then rejects it as unresolvable (a deliberate
    no-op; nothing to reconcile).
    """
    duration_s = outcome.duration_ms / 1000.0
    summary = (
        f"dispatch {dispatch_id}: {outcome.tool_call_count} tool calls, "
        f"{duration_s:.1f}s, {result_bytes}B -> sidecar"
    )
    if degraded_reason:
        summary = f"{summary} (degraded: {degraded_reason})"
    if offgit_deliverable_uris:
        summary = (
            f"{summary}; off-git deliverables: {len(offgit_deliverable_uris)} "
            f"({offgit_deliverable_uris[0]})"
        )
    status = _map_closeout_status(degraded_reason)
    if verification and any(v.exit_code for v in verification):
        status = CloseoutStatus.PARTIAL
    status = degrade_status_for_capture(status, capture_status, divergence_reason)
    manifest_source = effects_manifest or outcome.effects_manifest
    manifest_payload = serialize_effects_manifest_for_body(
        manifest_source,
        sidecar_appendix=sidecar_appendix,
    )
    cortex_assertions = harvest_cortex_assertion_ids(manifest_source)
    cortex_writes_unattributed = not cortex_assertions and cortex_surface_has_write_op(
        manifest_source
    )
    if cortex_writes_unattributed:
        cortex_assertions = None
        deviations = [*(deviations or []), "capture:cortex_writes_unattributed"]
    repo_files = change_set or ChangeSet(created=(), modified=(), deleted=())

    residue_paths = [
        *repo_files.created,
        *repo_files.modified,
        *repo_files.deleted,
        *(files_untracked_or_ignored or ()),
    ]
    propagation_residue = list(residue_actions(residue_paths))

    def _render_body(
        manifest_value: EffectsManifest | dict[str, Any] | None,
    ) -> str:
        closeout = ImplementCloseout(
            status=status,
            summary=summary,
            source_ref=work_item_ref or sidecar_ref,
            files_created=list(repo_files.created),
            files_modified=list(repo_files.modified),
            files_deleted=list(repo_files.deleted),
            capture_status=capture_status,
            effects_manifest=manifest_value
            if isinstance(manifest_value, EffectsManifest)
            else None,
            deviations=deviations or [],
            verification=verification or [],
            evidence_uris=EvidenceUris(
                artifact_paths=artifact_paths_for_closeout(
                    sidecar_ref,
                    cortex_artifact_paths or [],
                    cortex_first=cortex_first,
                    offgit_deliverable_uris=offgit_deliverable_uris or [],
                ),
                bus_threads=[thread_id],
                dispatch_ids=[dispatch_id],
                cortex_assertions=cortex_assertions,
            ),
            propagation_residue=propagation_residue,
        )
        payload = closeout.model_dump(mode="json")
        if files_untracked_or_ignored:
            payload["files_untracked_or_ignored"] = files_untracked_or_ignored
        if files_outside_repo:
            payload["files_outside_repo"] = files_outside_repo
        if offgit_deliverable_uris:
            payload["files_offgit_produced"] = offgit_deliverable_uris
        if dropped_non_file_entries:
            payload["dropped_non_file_entries"] = dropped_non_file_entries
        if manifest_value is not None and not isinstance(
            manifest_value, EffectsManifest
        ):
            payload["effects_manifest"] = manifest_value
        effects = attribution_effects_paths(
            created=repo_files.created,
            modified=repo_files.modified,
            deleted=repo_files.deleted,
            files_untracked_or_ignored=files_untracked_or_ignored or [],
        )
        payload["effects"] = list(effects)
        tracked_empty = not (
            repo_files.created or repo_files.modified or repo_files.deleted
        )
        if tracked_empty and effects:
            payload["summary"] = (
                f"{payload['summary']}; {len(effects)} path(s) touched "
                "(untracked/gitignored)"
            )
        return json.dumps(payload, separators=(",", ":"))

    body = _render_body(manifest_payload)
    if len(body) > MAX_TURN_BODY_CHARS and manifest_source is not None:
        compact_payload = compact_manifest_for_body(manifest_source)
        if sidecar_appendix is not None and not any(
            line.startswith("{") for line in sidecar_appendix
        ):
            sidecar_appendix.append(
                json.dumps(manifest_source.model_dump(mode="json"), indent=2)
            )
        body = _render_body(compact_payload)
    return body


def prepare_closeout_delivery(
    *,
    source_repo: Path,
    dispatch_id: str,
    outcome: SdkRunOutcome,
    degraded_reason: str | None,
    thread_id: str,
    work_item_ref: str | None,
    baseline: dict[str, Any] | None = None,
    packet_text: str | None = None,
    cortex_artifact_paths: list[str] | None = None,
    gate_d_created_rels: tuple[str, ...] = (),
    deliverables_expected: bool = False,
    divergent_rels: tuple[str, ...] = (),
    light_bounded_expected_paths: tuple[str, ...] = (),
    execution_id: str = "test-execution",
    post_closeout_sidecar_fn: Callable[..., dict[str, Any] | None] | None = None,
) -> CloseoutDelivery:
    """Sync closeout assembly (tests). Production uses ``prepare_closeout_delivery_async``."""
    return _assemble_closeout_delivery(
        source_repo=source_repo,
        dispatch_id=dispatch_id,
        outcome=outcome,
        degraded_reason=degraded_reason,
        thread_id=thread_id,
        work_item_ref=work_item_ref,
        baseline=baseline,
        packet_text=packet_text,
        cortex_artifact_paths=cortex_artifact_paths or [],
        gate_d_created_rels=gate_d_created_rels,
        deliverables_expected=deliverables_expected,
        divergent_rels=divergent_rels,
        light_bounded_expected_paths=light_bounded_expected_paths,
        execution_id=execution_id,
        post_closeout_sidecar_fn=post_closeout_sidecar_fn,
    )


async def prepare_closeout_delivery_async(
    *,
    source_repo: Path,
    dispatch_id: str,
    outcome: SdkRunOutcome,
    degraded_reason: str | None,
    thread_id: str,
    work_item_ref: str | None,
    baseline: dict[str, Any] | None = None,
    packet_text: str | None = None,
    deliverables_expected: bool = False,
    light_bounded_expected_paths: tuple[str, ...] = (),
    execution_id: str,
    extra_deviations: tuple[str, ...] = (),
    post_closeout_sidecar_fn: Callable[..., Any] | None = None,
    worktree_isolated: bool = False,
) -> CloseoutDelivery:
    """Write sidecar, resolve pinned cortex deliverables, build closeout JSON."""
    files_expected = _files_expected_for_pinning(
        packet_text,
        deliverables_expected,
        light_bounded_expected_paths,
    )
    text = full_result_text(outcome.body, degraded_reason)
    pinned = await resolve_cortex_pinned_deliverables(
        files_expected=files_expected,
        full_text=text,
        source_repo=source_repo,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    expected_rels = cortex_expected_rels(files_expected)
    gate_d_created = pinned.satisfied_rels
    if len(gate_d_created) < len(expected_rels):
        missing = [r for r in expected_rels if r not in gate_d_created]
        shown = ",".join(missing[:3])
        if len(missing) > 3:
            shown = f"{shown},+{len(missing) - 3}"
        pin_reason = f"pinned_deliverable_write_failed:{shown}"
        degraded_reason = degraded_reason or pin_reason
    return await _assemble_closeout_delivery_async(
        source_repo=source_repo,
        dispatch_id=dispatch_id,
        outcome=outcome,
        degraded_reason=degraded_reason,
        thread_id=thread_id,
        work_item_ref=work_item_ref,
        baseline=baseline,
        packet_text=packet_text,
        files_expected=files_expected,
        cortex_artifact_paths=pinned.uris,
        gate_d_created_rels=gate_d_created,
        deliverables_expected=deliverables_expected,
        divergent_rels=pinned.divergent_rels,
        light_bounded_expected_paths=light_bounded_expected_paths,
        execution_id=execution_id,
        extra_deviations=extra_deviations,
        post_closeout_sidecar_fn=post_closeout_sidecar_fn,
        worktree_isolated=worktree_isolated,
    )


def _assemble_closeout_delivery(
    *,
    source_repo: Path,
    dispatch_id: str,
    outcome: SdkRunOutcome,
    degraded_reason: str | None,
    thread_id: str,
    work_item_ref: str | None,
    baseline: dict[str, Any] | None,
    packet_text: str | None,
    files_expected: list[str] | None = None,
    cortex_artifact_paths: list[str],
    gate_d_created_rels: tuple[str, ...],
    deliverables_expected: bool = False,
    divergent_rels: tuple[str, ...] = (),
    light_bounded_expected_paths: tuple[str, ...] = (),
    execution_id: str = "test-execution",
    extra_deviations: tuple[str, ...] = (),
    post_closeout_sidecar_fn: Callable[..., dict[str, Any] | None] | None = None,
    finalize_oversize: bool = True,
    worktree_isolated: bool = False,
) -> CloseoutDelivery:
    """Assemble implement closeout delivery.

    Lane-A contract (a:25024): ``worktree_isolated`` defaults False on sole shared
    master. Ambient git/worktree census is visibility-only; never pass
    ``worktree_isolated=True`` here to tolerate parallel WIP — that poisons
    Lane-B isolation semantics. Isolated hard-fail paths activate only when a
    future Lane-B caller explicitly sets ``worktree_isolated=True``.
    """
    text = full_result_text(outcome.body, degraded_reason)
    sidecar_appendix: list[str] = []
    sidecar_path = write_repo_sidecar(source_repo, dispatch_id, text)
    sidecar_ref = sidecar_workspaces_ref(dispatch_id)
    result_bytes = len(text.encode("utf-8"))
    files_expected = (
        files_expected
        if files_expected is not None
        else _files_expected_from_packet(packet_text)
    )
    if baseline is None:
        git_change_set = ChangeSet(created=(), modified=(), deleted=())
        files_untracked_or_ignored: tuple[str, ...] = ()
        outside_repo_paths: tuple[str, ...] = ()
        baseline_deviations: list[str] = []
    else:
        git_change_set, files_untracked_or_ignored, outside_repo_paths = (
            reconcile_workspace_changes(
                source_repo=source_repo,
                baseline=baseline,
                manifest=outcome.effects_manifest,
            )
        )
        baseline_deviations = []
        if "outside_repo" not in baseline:
            baseline_deviations.append("capture:outside_repo_baseline_missing")
    manifest = merge_wrapper_manifest(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        base=outcome.effects_manifest,
        cortex_artifact_paths=cortex_artifact_paths,
        git_change_set=git_change_set,
    )
    offgit_uris = manifest_offgit_deliverable_uris(manifest, sidecar_ref=sidecar_ref)
    mount = resolve_mount_root(source_repo)
    manifest_cs, manifest_outside, dropped_non_file_entries = (
        repo_change_set_from_manifest(
            manifest,
            source_repo=source_repo,
            mount_root=mount,
        )
    )
    if manifest_cs is None:
        manifest_cs = ChangeSet(created=(), modified=(), deleted=())
    repo_change_set, manifest_extra_untracked, manifest_git_divergence = (
        resolve_repo_change_set(
            manifest=manifest,
            git_change_set=git_change_set,
            source_repo=source_repo,
            mount_root=mount,
        )
    )
    repo_change_set, files_untracked_or_ignored = partition_gitignored_from_change_set(
        repo_change_set,
        source_repo=source_repo,
        existing_untracked=(*files_untracked_or_ignored, *manifest_extra_untracked),
    )
    repo_change_set = ChangeSet(
        created=tuple(filter_manifest_swamp(repo_change_set.created)),
        modified=tuple(filter_manifest_swamp(repo_change_set.modified)),
        deleted=tuple(filter_manifest_swamp(repo_change_set.deleted)),
    )
    all_outside_repo = tuple(dict.fromkeys([*outside_repo_paths, *manifest_outside]))
    verification_cs = build_verification_change_set(
        repo_change_set, gate_d_created_rels
    )
    if baseline is None:
        verification = []
    else:
        verification = verify_deliverables(
            spec=None,
            change_set=verification_cs,
            outcome=outcome,
            sidecar_path=sidecar_path,
            files_expected=files_expected,
            baseline=baseline,
            source_repo=source_repo,
        )
        lint_verification, lint_deviation = run_touched_files_lint(
            source_repo, repo_change_set
        )
        verification = [*verification, lint_verification]
        if lint_deviation:
            baseline_deviations.append(lint_deviation)
    capture_status, divergence_reason, deviations, manifest = (
        resolve_closeout_capture_fields(
            deliverables_expected=deliverables_expected,
            baseline=baseline,
            files_expected=files_expected,
            degraded_reason=degraded_reason,
            change_set=git_change_set,
            divergent_rels=divergent_rels,
            source_repo=source_repo,
            cortex_root=cortex_files_root(),
            manifest=manifest,
            outside_repo_paths=all_outside_repo,
            files_untracked_or_ignored=files_untracked_or_ignored,
            mount_root=mount,
            light_bounded_expected_paths=light_bounded_expected_paths,
            worktree_isolated=worktree_isolated,
        )
    )
    # Caller-supplied tokens lead: oversize bodies keep only the first few
    # deviations, and a gate-bypass finding must not be the entry that is dropped.
    deviations = [
        *extra_deviations,
        *baseline_deviations,
        *(d for d in (deviations or []) if d not in extra_deviations),
    ]
    if outcome.stream_only_deviations:
        deviations = [
            *deviations,
            *(
                d
                for d in outcome.stream_only_deviations
                if d not in deviations
            ),
        ]
    for reason in outcome.degraded_reasons:
        token = f"degraded:{reason}"
        if token not in deviations and reason not in deviations:
            deviations.append(token)
    if dropped_non_file_entries:
        deviations = [*(deviations or []), "capture:non_file_manifest_entry_dropped"]
    expected_cortex_uris = collect_expected_cortex_deliverable_uris(
        light_bounded_expected_paths=light_bounded_expected_paths,
        files_expected=files_expected,
        cortex_artifact_paths=cortex_artifact_paths,
    )
    oob_deviations, oob_divergence = oob_cortex_write_findings(
        expected_cortex_uris=expected_cortex_uris,
        offgit_uris=offgit_uris,
        cortex_root=cortex_files_root(),
    )
    if oob_deviations:
        deviations = [*deviations, *oob_deviations]
        if capture_status == "complete":
            capture_status = "partial"
        if divergence_reason is None:
            divergence_reason = oob_divergence
    if (
        manifest_git_divergence
        and "divergence:manifest_vs_git_labels" not in deviations
    ):
        deviations = [*(deviations or []), "divergence:manifest_vs_git_labels"]
        if divergence_reason is None:
            divergence_reason = "divergence:manifest_vs_git_labels"
    cortex_authoritative = bool(gate_d_created_rels)
    body = build_implement_closeout_body(
        dispatch_id=dispatch_id,
        outcome=outcome,
        degraded_reason=degraded_reason,
        sidecar_ref=sidecar_ref,
        result_bytes=result_bytes,
        thread_id=thread_id,
        work_item_ref=work_item_ref,
        change_set=repo_change_set,
        verification=verification,
        cortex_artifact_paths=cortex_artifact_paths,
        capture_status=capture_status,
        divergence_reason=divergence_reason,
        deviations=deviations,
        effects_manifest=manifest,
        sidecar_appendix=sidecar_appendix,
        cortex_first=cortex_authoritative,
        files_untracked_or_ignored=list(files_untracked_or_ignored),
        files_outside_repo=list(all_outside_repo),
        offgit_deliverable_uris=offgit_uris,
        dropped_non_file_entries=dropped_non_file_entries,
    )
    if sidecar_appendix:
        appendix = "\n\n## effects_manifest\n\n" + "\n".join(sidecar_appendix)
        sidecar_path.write_text(
            sidecar_path.read_text(encoding="utf-8") + appendix,
            encoding="utf-8",
        )
        result_bytes = len(sidecar_path.read_text(encoding="utf-8").encode("utf-8"))
    full_body = body
    if len(full_body) > MAX_TURN_BODY_CHARS and finalize_oversize:
        body_relocated, tier = relocate_oversize_closeout_body_sync(
            full_body=full_body,
            sidecar_path=sidecar_path,
            sidecar_ref=sidecar_ref,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            post_closeout_sidecar_fn=post_closeout_sidecar_fn,
        )
        emit_sdk_closeout_relocated(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_id=execution_id,
            uri=body_relocated["uri"],
            body_chars=body_relocated["body_chars"],
            tier=tier,
        )
        body = finalize_closeout_body(full_body, body_relocated=body_relocated)
    parsed = json.loads(body)
    return CloseoutDelivery(
        body=body,
        sidecar_ref=sidecar_ref,
        sidecar_path=sidecar_path,
        full_result_bytes=result_bytes,
        closeout_status=CloseoutStatus(parsed["status"]),
    )


async def _assemble_closeout_delivery_async(
    *,
    source_repo: Path,
    dispatch_id: str,
    outcome: SdkRunOutcome,
    degraded_reason: str | None,
    thread_id: str,
    work_item_ref: str | None,
    baseline: dict[str, Any] | None,
    packet_text: str | None,
    files_expected: list[str] | None = None,
    cortex_artifact_paths: list[str],
    gate_d_created_rels: tuple[str, ...],
    deliverables_expected: bool = False,
    divergent_rels: tuple[str, ...] = (),
    light_bounded_expected_paths: tuple[str, ...] = (),
    execution_id: str,
    extra_deviations: tuple[str, ...] = (),
    post_closeout_sidecar_fn: Callable[..., Any] | None = None,
    worktree_isolated: bool = False,
) -> CloseoutDelivery:
    delivery = _assemble_closeout_delivery(
        source_repo=source_repo,
        dispatch_id=dispatch_id,
        outcome=outcome,
        degraded_reason=degraded_reason,
        thread_id=thread_id,
        work_item_ref=work_item_ref,
        baseline=baseline,
        packet_text=packet_text,
        files_expected=files_expected,
        cortex_artifact_paths=cortex_artifact_paths,
        gate_d_created_rels=gate_d_created_rels,
        deliverables_expected=deliverables_expected,
        divergent_rels=divergent_rels,
        light_bounded_expected_paths=light_bounded_expected_paths,
        execution_id=execution_id,
        extra_deviations=extra_deviations,
        finalize_oversize=False,
        worktree_isolated=worktree_isolated,
    )
    full_body = delivery.body
    if len(full_body) <= MAX_TURN_BODY_CHARS:
        return delivery
    body_relocated, tier = await relocate_oversize_closeout_body_async(
        full_body=full_body,
        sidecar_path=delivery.sidecar_path,
        sidecar_ref=delivery.sidecar_ref,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        post_closeout_sidecar_fn=post_closeout_sidecar_fn,
    )
    emit_sdk_closeout_relocated(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        execution_id=execution_id,
        uri=body_relocated["uri"],
        body_chars=body_relocated["body_chars"],
        tier=tier,
    )
    body = finalize_closeout_body(full_body, body_relocated=body_relocated)
    parsed = json.loads(body)
    return CloseoutDelivery(
        body=body,
        sidecar_ref=delivery.sidecar_ref,
        sidecar_path=delivery.sidecar_path,
        full_result_bytes=delivery.full_result_bytes,
        closeout_status=CloseoutStatus(parsed["status"]),
    )


def format_delivery_fallback_body(
    *,
    status_code: int,
    sidecar_ref: str,
    result_bytes: int,
) -> str:
    return (
        f"status: delivery_failed\n"
        f"bus_status_code: {status_code}\n"
        f"result_bytes: {result_bytes}\n"
        f"sidecar: {sidecar_ref}\n\n"
        "Closeout turn rejected by agent-bus. Full Composer result in sidecar."
    )


def resolve_run_outcome_label(degraded_reason: str | None) -> str:
    return "degraded" if degraded_reason else "ok"


def resolve_completion_outcome(*, run_outcome: str, delivery_ok: bool) -> str:
    if not delivery_ok:
        return "delivery_failed"
    return run_outcome
