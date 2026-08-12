"""Closeout validation and bounded delivery helpers for cursor-sdk worker dispatches."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.closeout_models import (
    AmbientRepoMovement,
    EffectsManifest,
    EvidenceUris,
    ImplementCloseout,
    Verification,
    derived_gate_verification,
    observed_process_verification,
)
from implement_admission.deliverable_verification import (
    evaluate_deliverable_verification,
)
from implement_admission.normalize import _files_from_packet
from implement_admission.propagation_row import (
    land_paths_for_propagation,
    resolve_code_ref,
)
from implement_admission.spec import (
    NO_RUN_DEGRADED_REASONS,
    CloseoutStatus,
    ImplementSpec,
    WorkOutcome,
)
from universal_logging import get_logger

from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_uri import (
    read_cortex_text,
)
from services.git_integration_worker.cursor_auto.episode_residue import (
    residue_actions,
    resolve_propagation_for_finalize,
)
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_ambient import ambient_deviation_token
from services.git_integration_worker.cursor_sdk_authored_status_reconcile import (
    reconcile_structured_with_authored,
)
from services.git_integration_worker.cursor_sdk_boundary_finalize import (
    finalize_boundary_manifest,
)
from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    apply_capture_incompleteness_gate,
    apply_escalation_harvest_gate,
    attribution_effects_paths,
    baseline_dirty_in_expected,
    filter_manifest_swamp,
    gitignored_manifest_paths,
    normalize_wt_baseline,
    partition_gitignored_from_change_set,
    project_status_from_work_outcome,
    resolve_closeout_capture_fields,
    resolve_work_outcome,
    verification_has_failure,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    artifact_paths_for_closeout,
    cortex_expected_rels,
    full_result_text,
    persist_structured_closeout_full_to_repo_sidecar,
    relocate_oversize_closeout_body_async,
    relocate_oversize_closeout_body_sync,
    resolve_cortex_pinned_deliverables,
    sidecar_workspaces_ref,
    write_repo_sidecar,
)
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_closeout_relocated,
)
from services.git_integration_worker.cursor_sdk_git_head import (
    observed_lane_git_refs,
    resolve_git_head,
    tip_window_meter_counts,
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
    serialize_effects_manifest_for_body,
    snapshot_outside_repo_paths,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    verification_change_set as build_verification_change_set,
)
from services.git_integration_worker.cursor_sdk_polarity import (
    ClaimedOp,
    git_concurs_deleted,
    list_git_deleted_paths,
    polarity_deviation_token,
    prove_polarity,
)
from services.git_integration_worker.cursor_sdk_repo_precedence import (
    resolve_repo_change_set,
)
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)
from services.git_integration_worker.cursor_sdk_subagent_capture import (
    ensure_subagents_surface,
)
from services.git_integration_worker.cursor_sdk_test_observation import (
    annotate_test_observation_discrepancy,
    append_harvest_demotion_deviations,
    extract_prose_test_claim,
    harvest_test_verifications,
)
from services.git_integration_worker.cursor_sdk_usage_sidecar import (
    render_usage_sidecar_section,
    stamp_usage_model_label,
)
from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

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


def capture_wt_baseline_with_hashes(
    source_repo: Path,
    *,
    mount_root: Path | None = None,
    repo_roots: list[Path] | tuple[Path, ...] | None = None,
) -> dict[str, Any] | None:
    """Porcelain codes plus content hashes and outside-repo census at admit."""
    codes = capture_wt_baseline(source_repo)
    if codes is None:
        return None
    hashes: dict[str, str] = {}
    for path in codes:
        digest = _hash_worktree_file(source_repo, path)
        if digest is not None:
            hashes[path] = digest
    mount = (mount_root or resolve_mount_root(source_repo)).resolve()
    roots = list(repo_roots) if repo_roots is not None else registered_repo_roots(mount)
    outside = sorted(snapshot_outside_repo_paths(mount, roots))
    return {
        "codes": codes,
        "hashes": hashes,
        "outside_repo": outside,
        "admit_head": resolve_git_head(source_repo),
    }


# Cap retained lint streams so a noisy ruff failure cannot inflate closeout JSON.
# Marker suffix records the cut when either stream exceeds the budget.
_LINT_OUTPUT_RETAIN_CHARS = 4000


def _decode_retained_stream(raw: bytes | str | None) -> tuple[str, bool]:
    """Decode a subprocess stream and truncate to ``_LINT_OUTPUT_RETAIN_CHARS``.

    Returns ``(text, truncated)``. Empty/absent streams become ``""``.
    """
    if raw is None:
        return "", False
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    if len(text) <= _LINT_OUTPUT_RETAIN_CHARS:
        return text, False
    return (
        text[:_LINT_OUTPUT_RETAIN_CHARS] + "\n...[truncated]",
        True,
    )


def run_touched_files_lint(
    source_repo: Path,
    change_set: ChangeSet,
) -> tuple[Verification, str | None]:
    """Run ``ruff check`` on touched ``*.py`` paths from the git change set.

    Each call mints a fresh ``invocation_id`` so this closeout-time process
    cannot be silently conflated with a mid-run agent shell that happened to
    print ``All checks passed!`` (specimen auto-00a23d2a4f45). ``cwd`` is
    pinned to ``source_repo`` so config discovery matches in-tree measurement.

    On non-zero exit, stdout/stderr are retained on the verification row
    (each truncated at ``_LINT_OUTPUT_RETAIN_CHARS``) so a later
    ``checks_failed`` grade remains interrogable.
    """
    py_paths = [
        path
        for path in (*change_set.created, *change_set.modified)
        if path.endswith(".py")
    ]
    if not py_paths:
        return (
            derived_gate_verification(
                command="ruff check (no python files touched)",
                exit_code=0,
                basis="lint_skipped_no_python",
                invocation_id=f"lint-skip:{uuid4().hex}",
            ),
            None,
        )
    abs_paths = [str(source_repo / path) for path in py_paths]
    command = f"ruff check {len(py_paths)} touched files"
    invocation_id = f"lint:{uuid4().hex}"
    try:
        # Pin cwd to the owning repo root so isort/first-party discovery matches
        # in-tree measurement (orphan cwd → phantom I001 on otherwise-clean files).
        proc = subprocess.run(
            ["ruff", "check", *abs_paths],
            capture_output=True,
            timeout=60,
            cwd=str(source_repo),
        )
    except FileNotFoundError:
        return (
            derived_gate_verification(
                command=command,
                exit_code=0,
                basis="lint_unavailable_ruff_missing",
                invocation_id=invocation_id,
            ),
            "verification:lint_unavailable",
        )
    except subprocess.TimeoutExpired:
        return (
            derived_gate_verification(
                command=command,
                exit_code=0,
                basis="lint_unavailable_timeout",
                invocation_id=invocation_id,
            ),
            "verification:lint_unavailable",
        )
    stdout: str | None = None
    stderr: str | None = None
    output_truncated = False
    if proc.returncode != 0:
        stdout, trunc_out = _decode_retained_stream(proc.stdout)
        stderr, trunc_err = _decode_retained_stream(proc.stderr)
        output_truncated = trunc_out or trunc_err
    return (
        observed_process_verification(
            command=command,
            exit_code=proc.returncode,
            invocation_id=invocation_id,
            basis="subprocess.run.returncode",
            stdout=stdout,
            stderr=stderr,
            output_truncated=output_truncated,
        ),
        None,
    )


def run_giw_subtree_f821_lint(
    source_repo: Path,
) -> tuple[Verification, str | None]:
    """Run ``ruff check --select F821`` on the GIW package subtree.

    Whole-repo ruff is blocked by pre-existing master lint debt; this
    F821-only pass on ``services/git_integration_worker/`` closes the
    enforcement gap where undefined-name defects in the dispatch substrate
    landed despite F821 being enabled project-wide (arc 6655).

    Grading-only at closeout — blocking enforcement lives in
    :func:`salvage_commit` and the ``git_land`` green gate (before land).
    """
    from services.git_integration_worker.giw_f821_gate import run_giw_subtree_f821_check

    invocation_id = f"lint-giw-f821:{uuid4().hex}"
    result = run_giw_subtree_f821_check(source_repo)
    if result.stderr.strip() == "ruff missing — gate skipped":
        return (
            derived_gate_verification(
                command=result.command,
                exit_code=0,
                basis="lint_unavailable_ruff_missing",
                invocation_id=invocation_id,
            ),
            "verification:lint_unavailable",
        )
    if result.exit_code == 124:
        return (
            derived_gate_verification(
                command=result.command,
                exit_code=0,
                basis="lint_unavailable_timeout",
                invocation_id=invocation_id,
            ),
            "verification:lint_unavailable",
        )
    stdout: str | None = None
    stderr: str | None = None
    output_truncated = False
    if result.exit_code != 0:
        stdout, trunc_out = _decode_retained_stream(result.stdout)
        stderr, trunc_err = _decode_retained_stream(result.stderr)
        output_truncated = trunc_out or trunc_err
    return (
        observed_process_verification(
            command=result.command,
            exit_code=result.exit_code,
            invocation_id=invocation_id,
            basis="subprocess.run.returncode",
            stdout=stdout,
            stderr=stderr,
            output_truncated=output_truncated,
        ),
        None,
    )


def _split_baseline(
    baseline: dict[str, Any] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    return normalize_wt_baseline(baseline)


def changed_paths(
    source_repo: Path, baseline: dict[str, Any] | None
) -> tuple[ChangeSet, tuple[str, ...]]:
    """Derive created/modified/deleted paths vs an admit-time baseline."""
    current = capture_wt_baseline(source_repo)
    if current is None:
        return ChangeSet(created=(), modified=(), deleted=()), ()
    codes, hashes = _split_baseline(baseline)
    admit_head: str | None = None
    if isinstance(baseline, dict):
        raw_head = baseline.get("admit_head")
        if isinstance(raw_head, str) and raw_head.strip():
            admit_head = raw_head.strip()
    git_deleted = list_git_deleted_paths(source_repo)
    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    deviations: list[str] = []
    all_paths = set(current) | set(codes) | git_deleted
    for path in sorted(all_paths):
        cur = current.get(path)
        prev = codes.get(path)
        claimed: ClaimedOp | None = None
        current_hash: str | None = None
        repo_path = source_repo / path
        is_gone = cur is None or not repo_path.exists()
        git_concurs_del = git_concurs_deleted(path, current, git_deleted)
        if is_gone and (
            prev is not None or (admit_head is not None and git_concurs_del)
        ):
            claimed = "deleted"
        elif cur is not None and prev is None:
            claimed = "created" if cur.startswith("?") else "modified"
            current_hash = _hash_worktree_file(source_repo, path)
        elif cur is not None and prev is not None and cur != prev:
            claimed = "modified"
            current_hash = _hash_worktree_file(source_repo, path)
        elif cur is not None and prev is not None and cur == prev and path in hashes:
            current_hash = _hash_worktree_file(source_repo, path)
            if current_hash is not None and current_hash != hashes[path]:
                claimed = "created" if prev.startswith("?") else "modified"
        if claimed is None:
            continue
        if prove_polarity(
            claimed=claimed,
            path=path,
            source_repo=source_repo,
            baseline_codes=codes,
            baseline_hashes=hashes,
            current_porcelain=current,
            current_hash=current_hash,
            git_deleted_paths=git_deleted,
            admit_head=admit_head,
        ):
            if claimed == "deleted":
                deleted.append(path)
            elif claimed == "created":
                created.append(path)
            else:
                modified.append(path)
        else:
            deviations.append(polarity_deviation_token(claimed, path))
    return (
        ChangeSet(
            created=tuple(created),
            modified=tuple(modified),
            deleted=tuple(deleted),
        ),
        tuple(deviations),
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
    repo_roots: list[Path] | tuple[Path, ...] | None = None,
) -> tuple[ChangeSet, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Multi-root disk reconciliation: git diff + outside-repo paths + gitignored."""
    from services.git_integration_worker.cursor_sdk_manifest import resolve_mount_root

    mount = (mount_root or resolve_mount_root(source_repo)).resolve()
    repos = list(repo_roots) if repo_roots is not None else registered_repo_roots(mount)
    if baseline is None:
        git_change = ChangeSet(created=(), modified=(), deleted=())
        polarity_deviations: tuple[str, ...] = ()
    else:
        git_change, polarity_deviations = changed_paths(source_repo, baseline)
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
    return git_change, gitignored, new_outside, polarity_deviations


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
    """Map SDK/bridge failures to class-derived ``degraded_reasons[]`` tokens.

    Tokens derive from the exception **class** (roadmap item 4), not
    stringified ``exc.code``.  Subclass-before-parent ``isinstance`` order
    respects the ``cursor_sdk.errors`` hierarchy.
    """
    from cursor_sdk.errors import (
        AgentBusyError,
        AgentNotFoundError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        ConfigurationError,
        CursorSDKError,
        IntegrationNotConnectedError,
        InternalServerError,
        NetworkError,
        NotFoundError,
        PermissionDeniedError,
        RateLimitError,
        UnsupportedRunOperationError,
    )

    from services.git_integration_worker.cursor_home import (
        CursorHomeConfigError,
        CursorVenvConfigError,
    )

    _sdk_class_tokens: tuple[tuple[type[BaseException], str], ...] = (
        (RateLimitError, "sdk_rate_limited"),
        (AgentBusyError, "sdk_agent_busy"),
        (AuthenticationError, "sdk_auth_failed"),
        (PermissionDeniedError, "sdk_permission_denied"),
        (AgentNotFoundError, "sdk_agent_not_found"),
        (NotFoundError, "sdk_run_not_found"),
        (APITimeoutError, "sdk_timeout"),
        (IntegrationNotConnectedError, "sdk_integration_not_connected"),
        (UnsupportedRunOperationError, "sdk_unsupported_run_operation"),
        (BadRequestError, "sdk_bad_request"),
        (ConfigurationError, "sdk_configuration"),
        (InternalServerError, "sdk_internal_server"),
        (NetworkError, "sdk_network"),
    )
    for exc_type, token in _sdk_class_tokens:
        if isinstance(exc, exc_type):
            return (token,)
    if isinstance(exc, CursorSDKError):
        code = getattr(exc, "code", None) or "unknown"
        return (f"sdk_error:{code}",)
    if type(exc).__name__ == "SdkRunAbortedError":
        cause = exc.__cause__
        if cause is not None:
            inner = degraded_reasons_from_exception(cause)
            if inner != ("worker_dispatch_failed",):
                return inner
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
    0-byte sidecar. Returns ``"empty_terminal_output"`` (mapped to FAILED by
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
    NO_RUN_*          -> FAILED   (run produced nothing — see NO_RUN_DEGRADED_REASONS)
    anything else     -> PARTIAL  (ran but degraded, e.g. pinned write miss)
    """
    if degraded_reason is None:
        return CloseoutStatus.COMPLETE
    if degraded_reason.startswith("run_status="):
        return CloseoutStatus.FAILED
    if degraded_reason in NO_RUN_DEGRADED_REASONS:
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
    if payload.get("work_outcome") is not None:
        reduced["work_outcome"] = payload["work_outcome"]
    if payload.get("status_incomplete_class") is not None:
        reduced["status_incomplete_class"] = payload["status_incomplete_class"]
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
    if payload.get("work_outcome") is not None:
        minimal["work_outcome"] = payload["work_outcome"]
    if payload.get("status_incomplete_class") is not None:
        minimal["status_incomplete_class"] = payload["status_incomplete_class"]
    if residue:
        minimal["propagation_residue"] = list(residue[:_CLOSEOUT_FILE_HEAD])
    if body_relocated is not None:
        minimal["body_relocated"] = body_relocated
    result = json.dumps(minimal, separators=(",", ":"))
    while len(result) > MAX_TURN_BODY_CHARS and len(minimal["summary"]) > 20:
        minimal["summary"] = str(minimal["summary"])[:-10]
        result = json.dumps(minimal, separators=(",", ":"))
    return result[:MAX_TURN_BODY_CHARS] if len(result) > MAX_TURN_BODY_CHARS else result


def _markdown_from_cortex_uris(uris: list[str]) -> list[str]:
    root = cortex_files_root()
    bodies: list[str] = []
    for uri in uris:
        if not uri.startswith("cortex://"):
            continue
        text = read_cortex_text(uri, cortex_root=root)
        if text:
            bodies.append(text)
    return bodies


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
    sidecar_markdown: str | None = None,
    extra_markdown_sources: list[str] | None = None,
    closeout_head: str | None = None,
    files_ambient_repo_movement: list[AmbientRepoMovement] | None = None,
    work_outcome: WorkOutcome | None = None,
    source_repo: Path | None = None,
    cortex_root: Path | None = None,
    light_bounded_expected_paths: tuple[str, ...] = (),
    files_expected: list[str] | None = None,
    baseline: dict[str, Any] | None = None,
    deliverables_expected: bool = False,
    lane: str | None = None,
    branch: str | None = None,
    branch_point: str | None = None,
    head_sha: str | None = None,
    commits_ahead: int | None = None,
    commits_ahead_unfiltered: int | None = None,
    landed: bool | None = None,
    isolation_materialized: bool | None = None,
    escalation_harvest: str | None = "none",
    resolved_model: str | None = None,
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
    artifact_paths = artifact_paths_for_closeout(
        sidecar_ref,
        cortex_artifact_paths or [],
        cortex_first=cortex_first,
        offgit_deliverable_uris=offgit_deliverable_uris or [],
    )
    resolved_work_outcome = work_outcome
    if (
        resolved_work_outcome is None
        and source_repo is not None
        and cortex_root is not None
    ):
        resolved_work_outcome = resolve_work_outcome(
            degraded_reason=degraded_reason,
            verification=verification,
            files_offgit_produced=offgit_deliverable_uris or [],
            artifact_paths=artifact_paths,
            light_bounded_expected_paths=light_bounded_expected_paths,
            files_expected=files_expected,
            manifest=effects_manifest or outcome.effects_manifest,
            source_repo=source_repo,
            cortex_root=cortex_root,
            baseline=baseline,
            divergence_reason=divergence_reason,
            deviations=deviations,
            deliverables_expected=deliverables_expected,
        )
    elif (
        resolved_work_outcome is None
        and verification
        and verification_has_failure(verification)
    ):
        resolved_work_outcome = WorkOutcome.CHECKS_FAILED
        status = project_status_from_work_outcome(
            resolved_work_outcome, degraded_reason
        )
    if resolved_work_outcome is not None:
        status = project_status_from_work_outcome(
            resolved_work_outcome, degraded_reason
        )
    status, resolved_work_outcome = apply_capture_incompleteness_gate(
        status=status,
        work_outcome=resolved_work_outcome,
        deliverables_expected=deliverables_expected,
        capture_status=capture_status,
    )
    status, resolved_work_outcome = apply_escalation_harvest_gate(
        status=status,
        work_outcome=resolved_work_outcome,
        escalation_harvest=escalation_harvest,
    )
    status, resolved_work_outcome, status_authority_disagreement, deviations = (
        reconcile_structured_with_authored(
            status=status,
            work_outcome=resolved_work_outcome,
            sidecar_markdown=sidecar_markdown,
            deviations=deviations,
            deliverables_expected=deliverables_expected,
        )
    )
    from services.git_integration_worker.cursor_sdk_land_discipline import (
        apply_lane_b_land_incompleteness,
    )

    pre_lane_b_status = status
    status, deviations = apply_lane_b_land_incompleteness(
        status,
        lane=lane,
        landed=landed,
        commits_ahead=commits_ahead,
        deviations=deviations,
    )
    from services.git_integration_worker.cursor_auto.closeout_status_polarity import (
        classify_status_incomplete_class,
    )

    status_incomplete_class = classify_status_incomplete_class(
        status=status,
        work_outcome=resolved_work_outcome,
        capture_status=capture_status,
        escalation_harvest=escalation_harvest,
        deviations=deviations,
        degraded_reason=degraded_reason,
    )
    if status_authority_disagreement is not None and status != pre_lane_b_status:
        from services.git_integration_worker.cursor_sdk_authored_status_reconcile import (
            refresh_disagreement_after_machine_gate,
        )

        status_authority_disagreement = refresh_disagreement_after_machine_gate(
            disagreement=status_authority_disagreement,
            post_gate_status=status,
            post_gate_work_outcome=resolved_work_outcome,
            pre_gate_status=pre_lane_b_status,
            status_incomplete_class=status_incomplete_class,
        )
    manifest_source = ensure_subagents_surface(
        effects_manifest or outcome.effects_manifest
    )
    manifest_payload = serialize_effects_manifest_for_body(
        manifest_source,
        sidecar_appendix=sidecar_appendix,
    )
    cortex_assertions = harvest_cortex_assertion_ids(manifest_source)
    cortex_section = manifest_source.surfaces.get("cortex") if manifest_source else None
    cortex_self_reported = (
        cortex_section is not None and cortex_section.authority_class == "self_reported"
    )
    cortex_writes_unattributed = (
        cortex_self_reported
        and not cortex_assertions
        and cortex_surface_has_write_op(manifest_source)
    )
    if cortex_writes_unattributed:
        cortex_assertions = None
        deviations = [*(deviations or []), "capture:cortex_writes_unattributed"]
    repo_files = change_set or ChangeSet(created=(), modified=(), deleted=())

    land_paths = land_paths_for_propagation(
        created=repo_files.created,
        modified=repo_files.modified,
        untracked=files_untracked_or_ignored or (),
    )
    propagation_residue = list(residue_actions(land_paths))
    markdown_sources = [*(extra_markdown_sources or [])]
    if sidecar_markdown and sidecar_markdown.strip():
        markdown_sources.append(sidecar_markdown)
    code_probe: dict[str, Any] = {}
    lane_git_refs: list[str] = []
    if source_repo is not None:
        admit_head = None
        if isinstance(baseline, dict):
            raw_admit = baseline.get("admit_head")
            if isinstance(raw_admit, str) and raw_admit.strip():
                admit_head = raw_admit.strip()
        lane_git_refs = observed_lane_git_refs(
            source_repo,
            dispatch_id=dispatch_id,
            admit_head=admit_head,
            closeout_head=closeout_head,
        )
    if lane_git_refs:
        code_probe = {"evidence_uris": {"git_refs": lane_git_refs}}
    elif outcome.sdk_git and isinstance(outcome.sdk_git, dict):
        head = outcome.sdk_git.get("HEAD") or outcome.sdk_git.get("head")
        if isinstance(head, str) and head.strip():
            code_probe = {"evidence_uris": {"git_refs": [head.strip()]}}
    if closeout_head:
        code_probe = {**code_probe, "closeout_head": closeout_head}
    propagation_rows = resolve_propagation_for_finalize(
        residue_paths=land_paths,
        markdown_sources=markdown_sources,
        code_ref=resolve_code_ref(code_probe),
    )

    def _render_body(
        manifest_value: EffectsManifest | dict[str, Any] | None,
    ) -> str:
        closeout = ImplementCloseout(
            status=status,
            work_outcome=resolved_work_outcome,
            escalation_harvest=escalation_harvest or "none",
            summary=summary,
            source_ref=work_item_ref or sidecar_ref,
            files_created=list(repo_files.created),
            files_modified=list(repo_files.modified),
            files_deleted=list(repo_files.deleted),
            files_ambient_repo_movement=list(files_ambient_repo_movement or []),
            capture_status=capture_status,
            effects_manifest=manifest_value
            if isinstance(manifest_value, EffectsManifest)
            else None,
            deviations=deviations or [],
            verification=verification or [],
            evidence_uris=EvidenceUris(
                artifact_paths=artifact_paths,
                bus_threads=[thread_id],
                dispatch_ids=[dispatch_id],
                cortex_assertions=cortex_assertions,
                git_refs=lane_git_refs
                or (
                    code_probe.get("evidence_uris", {}).get("git_refs", [])
                    if isinstance(code_probe.get("evidence_uris"), dict)
                    else []
                ),
            ),
            propagation_residue=propagation_residue,
            propagation=list(propagation_rows),
            usage=stamp_usage_model_label(outcome.usage, resolved_model),
            usage_capture_status=outcome.usage_capture_status,  # type: ignore[arg-type]
        )
        payload = closeout.model_dump(mode="json")
        if degraded_reason:
            payload["degraded_reason"] = degraded_reason
        payload["tool_call_count"] = outcome.tool_call_count
        if status_authority_disagreement is not None:
            payload["status_authority_disagreement"] = status_authority_disagreement
        if status_incomplete_class is not None:
            payload["status_incomplete_class"] = status_incomplete_class
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
        if lane is not None:
            payload["lane"] = lane
        if isolation_materialized is not None:
            payload["isolation_materialized"] = isolation_materialized
        if branch is not None:
            payload["branch"] = branch
        if branch_point is not None:
            payload["branch_point"] = branch_point
        if head_sha is not None:
            payload["head_sha"] = head_sha
        if commits_ahead is not None:
            payload["commits_ahead"] = commits_ahead
        else:
            # Schema model_dump may emit null; absence must travel as a missing
            # key so the plane classifier does not see a present measurement.
            payload.pop("commits_ahead", None)
        if commits_ahead_unfiltered is not None:
            payload["commits_ahead_unfiltered"] = commits_ahead_unfiltered
        else:
            payload.pop("commits_ahead_unfiltered", None)
        if landed is not None:
            payload["landed"] = landed
        effects = attribution_effects_paths(
            created=repo_files.created,
            modified=repo_files.modified,
            deleted=repo_files.deleted,
            files_untracked_or_ignored=files_untracked_or_ignored or [],
        )
        # §4.7 / a:26354 — off-git cortex URIs belong in effects so operator
        # disposition via closeout schema sees durable writes even when the repo ChangeSet is empty.
        merged_effects: list[str] = list(effects)
        seen_effects = set(merged_effects)
        for uri in offgit_deliverable_uris or []:
            if uri and uri not in seen_effects:
                seen_effects.add(uri)
                merged_effects.append(uri)
        payload["effects"] = merged_effects
        tracked_empty = not (
            repo_files.created or repo_files.modified or repo_files.deleted
        )
        if tracked_empty and merged_effects:
            payload["summary"] = (
                f"{payload['summary']}; {len(merged_effects)} path(s) touched "
                "(untracked/gitignored/off-git)"
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


def _capture_trees(
    source_repo: Path,
    binding: CaptureBinding | None,
) -> tuple[Path, Path, Path]:
    if binding is None:
        return source_repo, source_repo, resolve_mount_root(source_repo)
    return binding.write_tree, binding.receipt_tree, binding.mount_root


def prepare_closeout_delivery(
    *,
    source_repo: Path,
    binding: CaptureBinding | None = None,
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
    resolved_model: str | None = None,
) -> CloseoutDelivery:
    """Sync closeout assembly (tests). Production uses ``prepare_closeout_delivery_async``."""
    return _assemble_closeout_delivery(
        source_repo=source_repo,
        binding=binding,
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
        resolved_model=resolved_model,
    )


async def prepare_closeout_delivery_async(
    *,
    source_repo: Path,
    binding: CaptureBinding | None = None,
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
    resolved_model: str | None = None,
) -> CloseoutDelivery:
    """Write sidecar, resolve pinned cortex deliverables, build closeout JSON."""
    write_tree, _, _ = _capture_trees(source_repo, binding)
    files_expected = _files_expected_for_pinning(
        packet_text,
        deliverables_expected,
        light_bounded_expected_paths,
    )
    text = full_result_text(outcome.body, degraded_reason)
    pinned = await resolve_cortex_pinned_deliverables(
        files_expected=files_expected,
        full_text=text,
        source_repo=write_tree,
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
        binding=binding,
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
        resolved_model=resolved_model,
    )


def _register_cursor_sdk_seat_writes(
    *,
    dispatch_id: str,
    baseline: dict[str, Any] | None,
    repo_change_set: ChangeSet,
) -> None:
    """Register attributed closeout paths for lane-A cursor-sdk Rank-2 authorship.

    Attach at closeout (not admit) because authored paths are unknown until
    ``resolve_repo_change_set`` completes. Register the attributed set only —
    ambient/parallel-WIP diverted by resolve must not seed the ledger. Arc stays
    open (never ``close_arc`` here) so lane-B quiescent sweep does not commit
    cursor-sdk rows. ``source_repo`` uses the consumer key from ``load_config``
    (matches ``nested_outcome`` relay); Lane-B/worktree binding divergence can
    leave rows unread at a different resolved path.
    """
    if baseline is None:
        return
    paths = (
        *repo_change_set.created,
        *repo_change_set.modified,
        *repo_change_set.deleted,
    )
    if not paths:
        return
    source_repo = str(Path(load_config().source_repo).resolve())
    SeatWriteLedger.instance().register_paths(
        arc_id=dispatch_id,
        seat_id="cursor-sdk",
        source_repo=source_repo,
        paths=paths,
    )


def _assemble_closeout_delivery(
    *,
    source_repo: Path,
    binding: CaptureBinding | None = None,
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
    resolved_model: str | None = None,
) -> CloseoutDelivery:
    """Assemble implement closeout delivery.

    Lane-A contract (a:25024): ``worktree_isolated`` defaults False on sole shared
    master. Ambient git/worktree census is visibility-only; never pass
    ``worktree_isolated=True`` here to tolerate parallel WIP — that poisons
    Lane-B isolation semantics. Isolated hard-fail paths activate only when a
    future Lane-B caller explicitly sets ``worktree_isolated=True``.
    """
    write_tree, receipt_tree, mount = _capture_trees(source_repo, binding)
    repo_roots = list(binding.repo_roots) if binding is not None else None
    text = full_result_text(outcome.body, degraded_reason)
    sidecar_appendix: list[str] = []
    sidecar_path = write_repo_sidecar(receipt_tree, dispatch_id, text)
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
        (
            git_change_set,
            files_untracked_or_ignored,
            outside_repo_paths,
            polarity_deviations,
        ) = reconcile_workspace_changes(
            source_repo=write_tree,
            baseline=baseline,
            manifest=outcome.effects_manifest,
            mount_root=mount,
            repo_roots=repo_roots,
        )
        baseline_deviations = list(polarity_deviations)
        if "outside_repo" not in baseline:
            baseline_deviations.append("capture:outside_repo_baseline_missing")
    manifest = merge_wrapper_manifest(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        base=outcome.effects_manifest,
        cortex_artifact_paths=cortex_artifact_paths,
        git_change_set=git_change_set,
    )
    manifest, boundary_deviations = finalize_boundary_manifest(
        manifest,
        tool_calls=outcome.tool_calls,
        source_repo=receipt_tree,
        ledger=CursorDispatchLedger.instance(),
        parent_dispatch_id=dispatch_id,
    )
    offgit_uris = manifest_offgit_deliverable_uris(manifest, sidecar_ref=sidecar_ref)
    manifest_cs, manifest_outside, dropped_non_file_entries = (
        repo_change_set_from_manifest(
            manifest,
            source_repo=write_tree,
            mount_root=mount,
            repo_roots=repo_roots,
        )
    )
    if manifest_cs is None:
        manifest_cs = ChangeSet(created=(), modified=(), deleted=())
    (
        repo_change_set,
        manifest_extra_untracked,
        manifest_git_divergence,
        ambient_movements,
    ) = resolve_repo_change_set(
        manifest=manifest,
        git_change_set=git_change_set,
        source_repo=write_tree,
        mount_root=mount,
        baseline=baseline,
        files_expected=files_expected,
        current_porcelain=capture_wt_baseline(write_tree),
        admit_head=(
            baseline.get("admit_head")
            if isinstance(baseline, dict)
            and isinstance(baseline.get("admit_head"), str)
            else None
        ),
        closeout_head=resolve_git_head(write_tree),
        dispatch_id=dispatch_id,
    )
    repo_change_set, files_untracked_or_ignored = partition_gitignored_from_change_set(
        repo_change_set,
        source_repo=write_tree,
        existing_untracked=(*files_untracked_or_ignored, *manifest_extra_untracked),
    )
    repo_change_set = ChangeSet(
        created=tuple(filter_manifest_swamp(repo_change_set.created)),
        modified=tuple(filter_manifest_swamp(repo_change_set.modified)),
        deleted=tuple(filter_manifest_swamp(repo_change_set.deleted)),
    )
    _register_cursor_sdk_seat_writes(
        dispatch_id=dispatch_id,
        baseline=baseline,
        repo_change_set=repo_change_set,
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
            source_repo=write_tree,
        )
        lint_verification, lint_deviation = run_touched_files_lint(
            write_tree, repo_change_set
        )
        verification = [*verification, lint_verification]
        if lint_deviation:
            baseline_deviations.append(lint_deviation)
        giw_f821_verification, giw_f821_deviation = run_giw_subtree_f821_lint(
            write_tree
        )
        verification = [*verification, giw_f821_verification]
        if giw_f821_deviation:
            baseline_deviations.append(giw_f821_deviation)
    # Harvest observed pytest siblings from stream tool_calls regardless of
    # baseline / contract (G1: non-implement harvest still owed). Absence does
    # not earn "no tests ran" (presence_legible_absence_not).
    verification = [
        *verification,
        *harvest_test_verifications(outcome.tool_calls),
    ]
    baseline_deviations_list = list(baseline_deviations)
    append_harvest_demotion_deviations(verification, baseline_deviations_list)
    prose_exit, prose_claims_pytest = extract_prose_test_claim(text)
    discrepancy = annotate_test_observation_discrepancy(
        prose_claim_exit=prose_exit,
        prose_claims_pytest=prose_claims_pytest,
        verification=verification,
    )
    if discrepancy:
        baseline_deviations_list.append(discrepancy)
    baseline_deviations = baseline_deviations_list
    capture_status, divergence_reason, deviations, manifest = (
        resolve_closeout_capture_fields(
            deliverables_expected=deliverables_expected,
            baseline=baseline,
            files_expected=files_expected,
            degraded_reason=degraded_reason,
            change_set=git_change_set,
            divergent_rels=divergent_rels,
            source_repo=write_tree,
            cortex_root=cortex_files_root(),
            manifest=manifest,
            outside_repo_paths=all_outside_repo,
            files_untracked_or_ignored=files_untracked_or_ignored,
            mount_root=mount,
            light_bounded_expected_paths=light_bounded_expected_paths,
            worktree_isolated=worktree_isolated,
            read_only=CursorDispatchLedger.instance().read_read_only(
                dispatch_id=dispatch_id
            ),
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            lane=binding.lane if binding is not None else None,
        )
    )
    # Caller-supplied tokens lead: oversize bodies keep only the first few
    # deviations, and a gate-bypass finding must not be the entry that is dropped.
    deviations = [
        *extra_deviations,
        *baseline_deviations,
        *(
            d
            for d in (deviations or [])
            if d not in extra_deviations
            and not str(d).startswith(
                "divergence:repo_diff_paths_unattributed:ambient:"
            )
        ),
    ]
    if outcome.stream_only_deviations:
        deviations = [
            *deviations,
            *(d for d in outcome.stream_only_deviations if d not in deviations),
        ]
    if boundary_deviations:
        deviations = [
            *deviations,
            *(d for d in boundary_deviations if d not in deviations),
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
    ambient_token = ambient_deviation_token(ambient_movements)
    if ambient_token and ambient_token not in deviations:
        deviations = [*(deviations or []), ambient_token]
    lane_b_lane: str | None = None
    lane_b_branch: str | None = None
    lane_b_branch_point: str | None = None
    lane_b_head_sha: str | None = None
    lane_b_commits_ahead: int | None = None
    lane_b_landed: bool | None = None
    if binding is not None and binding.lane == "B":
        from services.git_integration_worker.cursor_sdk_lane_b_commit import (
            branch_state,
            commit_on_terminal,
        )
        from services.git_integration_worker.cursor_sdk_worktree import (
            lookup_dispatch_worktree,
        )

        record = lookup_dispatch_worktree(dispatch_id=dispatch_id)
        if record is not None:
            commit_result = commit_on_terminal(
                dispatch_id=dispatch_id,
                worktree_path=write_tree,
                branch_name=record.branch_name,
            )
            state = branch_state(
                binding.receipt_tree,
                branch_name=record.branch_name,
                branch_point=record.branch_point,
            )
            if (
                commit_result.committed
                and commit_result.head_sha
                and state.commits_ahead is not None
            ):
                from services.git_integration_worker.cursor_sdk_events import (
                    emit_sdk_lane_b_committed,
                )

                files_committed = len(repo_change_set.created) + len(
                    repo_change_set.modified
                )
                emit_sdk_lane_b_committed(
                    dispatch_id=dispatch_id,
                    thread_id=thread_id,
                    head_sha=commit_result.head_sha,
                    commits_ahead=state.commits_ahead,
                    files_committed=files_committed,
                )
            if commit_result.refused:
                # Work exists in the worktree but git declined to record it; the
                # dispatch cannot be graded as shipped off an uncommitted tree.
                refusal_token = (
                    f"divergence:lane_b_commit_refused:{commit_result.short_error}"
                )
                deviations = [*(deviations or []), refusal_token]
                if divergence_reason is None:
                    divergence_reason = "divergence:lane_b_commit_refused"
            lane_b_lane = "B"
            lane_b_branch = record.branch_name
            lane_b_branch_point = record.branch_point
            lane_b_head_sha = state.head_sha
            lane_b_commits_ahead = state.commits_ahead
            # landed@local-master — ancestry probe + G₂ meter; unknown stays None.
            from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
                probe_three_planes,
            )

            plane_obs = probe_three_planes(
                binding.receipt_tree,
                head_sha=state.head_sha,
                branch=record.branch_name,
            )
            # G₂: measured 0 refuses vacuous True; unknown ancestry/meter → None.
            from services.git_integration_worker.cursor_sdk_deliverables_expected import (
                admit_landed_true,
            )

            lane_b_landed = admit_landed_true(
                ancestry_on_master=plane_obs.landed_local_master,
                commits_ahead=state.commits_ahead,
            )
            if outcome.status != "finished" and not state.safe_to_delete:
                from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
                    mark_lane_b_disposition,
                )

                mark_lane_b_disposition(
                    branch_name=record.branch_name,
                    reason="abandoned",
                    dispatch_id=dispatch_id,
                    tip_sha=state.head_sha,
                )
    reported_lane = binding.lane if binding is not None else None
    isolation_mat: bool | None = None
    escalation_harvest: str = "none"
    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT record_json, lease_key, source_repo FROM cursor_sdk_dispatches "
            "WHERE dispatch_id = ?",
            (dispatch_id,),
        ).fetchone()
    if row is not None:
        from services.git_integration_worker.cursor_sdk_capacity_invariant import (
            resolve_isolation_materialized,
        )

        isolation_mat = resolve_isolation_materialized(
            record_json=row["record_json"],
            lease_key=row["lease_key"],
            source_repo=row["source_repo"],
        )
        try:
            record_data = json.loads(row["record_json"] or "{}")
        except json.JSONDecodeError:
            record_data = {}
        raw_harvest = record_data.get("escalation_harvest")
        if isinstance(raw_harvest, str) and raw_harvest.strip():
            escalation_harvest = raw_harvest.strip()
    cortex_authoritative = bool(gate_d_created_rels)
    closeout_head = resolve_git_head(write_tree)
    # Lane-A: populate capture head_sha from write-tree tip when Lane-B did not
    # assign one — keys the three-plane probe without upgrading from checkpoint prose.
    capture_head_sha = lane_b_head_sha if lane_b_head_sha is not None else closeout_head
    # Lane-A: populate commits_ahead from admit_head..closeout_head (symmetric with
    # Lane-B branch_point..branch). A real admit_head with an empty range is a
    # measured 0 (refuse vacuous landed). A missing/unresolvable admit_head must
    # leave the key absent — never launder None into 0 (presence typing travels).
    capture_commits_ahead = lane_b_commits_ahead
    capture_commits_ahead_unfiltered: int | None = None
    capture_landed = lane_b_landed
    if capture_commits_ahead is None:
        admit_head: str | None = None
        if isinstance(baseline, dict):
            raw_admit = baseline.get("admit_head")
            if isinstance(raw_admit, str) and raw_admit.strip():
                admit_head = raw_admit.strip()
        if admit_head is not None and closeout_head is not None:
            meter_pair = tip_window_meter_counts(
                write_tree,
                dispatch_id=dispatch_id,
                admit_head=admit_head,
                closeout_head=closeout_head,
            )
            if meter_pair is not None:
                capture_commits_ahead, capture_commits_ahead_unfiltered = meter_pair
        if lane_b_lane != "B" and capture_commits_ahead is not None:
            from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
                probe_three_planes,
            )
            from services.git_integration_worker.cursor_sdk_deliverables_expected import (
                admit_landed_true,
            )

            plane_obs = probe_three_planes(
                receipt_tree,
                head_sha=capture_head_sha,
                branch=lane_b_branch,
            )
            capture_landed = admit_landed_true(
                ancestry_on_master=plane_obs.landed_local_master,
                commits_ahead=capture_commits_ahead,
            )
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
        sidecar_markdown=text,
        extra_markdown_sources=_markdown_from_cortex_uris(
            list({*(cortex_artifact_paths or []), *offgit_uris})
        ),
        closeout_head=closeout_head,
        files_ambient_repo_movement=ambient_movements,
        source_repo=write_tree,
        cortex_root=cortex_files_root(),
        light_bounded_expected_paths=light_bounded_expected_paths,
        files_expected=files_expected,
        baseline=baseline,
        deliverables_expected=deliverables_expected,
        lane=lane_b_lane if lane_b_lane is not None else reported_lane,
        branch=lane_b_branch,
        branch_point=lane_b_branch_point,
        head_sha=capture_head_sha,
        commits_ahead=capture_commits_ahead,
        commits_ahead_unfiltered=capture_commits_ahead_unfiltered,
        landed=capture_landed,
        isolation_materialized=isolation_mat,
        escalation_harvest=escalation_harvest,
        resolved_model=resolved_model,
    )
    usage_section = render_usage_sidecar_section(
        usage=outcome.usage,
        usage_capture_status=outcome.usage_capture_status,
        resolved_model=resolved_model,
    )
    sidecar_suffix_parts: list[str] = []
    if sidecar_appendix:
        sidecar_suffix_parts.append(
            "\n\n## effects_manifest\n\n" + "\n".join(sidecar_appendix)
        )
    if usage_section:
        sidecar_suffix_parts.append("\n\n" + usage_section)
    if sidecar_suffix_parts:
        sidecar_path.write_text(
            sidecar_path.read_text(encoding="utf-8") + "".join(sidecar_suffix_parts),
            encoding="utf-8",
        )
        result_bytes = len(sidecar_path.read_text(encoding="utf-8").encode("utf-8"))
    full_body = body
    receipt_deviation = persist_structured_closeout_full_to_repo_sidecar(
        sidecar_path=sidecar_path,
        full_body=full_body,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    if receipt_deviation:
        payload = json.loads(full_body)
        payload_deviations = list(payload.get("deviations") or [])
        if receipt_deviation not in payload_deviations:
            payload_deviations.append(receipt_deviation)
        payload["deviations"] = payload_deviations
        if payload.get("status") != "failed":
            payload["capture_status"] = "partial"
        full_body = json.dumps(payload, separators=(",", ":"))
        body = full_body
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
    binding: CaptureBinding | None = None,
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
    resolved_model: str | None = None,
) -> CloseoutDelivery:
    delivery = _assemble_closeout_delivery(
        source_repo=source_repo,
        binding=binding,
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
        resolved_model=resolved_model,
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
