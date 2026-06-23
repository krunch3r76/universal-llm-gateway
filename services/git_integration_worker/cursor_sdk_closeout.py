"""Closeout validation and bounded delivery helpers for cursor-sdk worker dispatches."""

from __future__ import annotations

import hashlib
import json
import subprocess
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

from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    baseline_dirty_in_expected,
    degrade_status_for_capture,
    normalize_wt_baseline,
    resolve_closeout_capture_fields,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    artifact_paths_for_closeout,
    cortex_expected_rels,
    full_result_text,
    resolve_cortex_pinned_deliverables,
    sidecar_workspaces_ref,
    write_repo_sidecar,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    CaptureBranch,
    compact_manifest_for_body,
    merge_wrapper_manifest,
    no_capture_degraded_reason,
    resolve_repo_change_set,
    serialize_effects_manifest_for_body,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    verification_change_set as build_verification_change_set,
)

logger = get_logger(__name__)

# Must stay aligned with ``libs/agent_bus_store/turns_models`` bus invariants.
MAX_TURN_BODY_CHARS = 8_000


@dataclass(frozen=True)
class SdkRunOutcome:
    body: str
    status: str
    duration_ms: int
    tool_call_count: int
    effects_manifest: EffectsManifest | None = None
    capture_branch: CaptureBranch | None = None


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
    """Porcelain codes plus content hashes for paths dirty at admit."""
    codes = capture_wt_baseline(source_repo)
    if codes is None:
        return None
    hashes: dict[str, str] = {}
    for path in codes:
        digest = _hash_worktree_file(source_repo, path)
        if digest is not None:
            hashes[path] = digest
    return {"codes": codes, "hashes": hashes}


def _split_baseline(
    baseline: dict[str, Any] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    return normalize_wt_baseline(baseline)


def changed_paths(
    source_repo: Path, baseline: dict[str, Any] | None
) -> ChangeSet:
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


def _baseline_dirty_in_expected(
    baseline: dict[str, Any] | None, files_expected: list[str]
) -> bool:
    return baseline_dirty_in_expected(baseline, files_expected)


def _files_expected_from_packet(packet_text: str | None) -> list[str]:
    if not packet_text:
        return []
    return _files_from_packet(packet_text)


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
    status = _map_closeout_status(degraded_reason)
    if verification and any(v.exit_code for v in verification):
        status = CloseoutStatus.PARTIAL
    status = degrade_status_for_capture(status, capture_status, divergence_reason)
    manifest_source = effects_manifest or outcome.effects_manifest
    manifest_payload = serialize_effects_manifest_for_body(
        manifest_source,
        sidecar_appendix=sidecar_appendix,
    )
    repo_files = change_set or ChangeSet(created=(), modified=(), deleted=())

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
                ),
                bus_threads=[thread_id],
                dispatch_ids=[dispatch_id],
            ),
        )
        payload = closeout.model_dump(mode="json")
        if manifest_value is not None and not isinstance(manifest_value, EffectsManifest):
            payload["effects_manifest"] = manifest_value
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
    assert len(body) <= MAX_TURN_BODY_CHARS, (
        f"structured closeout body exceeded {MAX_TURN_BODY_CHARS} chars "
        f"(len={len(body)}); dispatch_id={dispatch_id}"
    )
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
) -> CloseoutDelivery:
    """Write sidecar, resolve pinned cortex deliverables, build closeout JSON."""
    files_expected = _files_expected_from_packet(packet_text)
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
    return _assemble_closeout_delivery(
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
) -> CloseoutDelivery:
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
    git_change_set = (
        ChangeSet(created=(), modified=(), deleted=())
        if baseline is None
        else changed_paths(source_repo, baseline)
    )
    manifest = merge_wrapper_manifest(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        base=outcome.effects_manifest,
        cortex_artifact_paths=cortex_artifact_paths,
    )
    repo_change_set = resolve_repo_change_set(
        manifest=manifest,
        git_change_set=git_change_set,
    )
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
        )
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
    )
    if sidecar_appendix:
        appendix = "\n\n## effects_manifest\n\n" + "\n".join(sidecar_appendix)
        sidecar_path.write_text(
            sidecar_path.read_text(encoding="utf-8") + appendix,
            encoding="utf-8",
        )
        result_bytes = len(sidecar_path.read_text(encoding="utf-8").encode("utf-8"))
    parsed = json.loads(body)
    return CloseoutDelivery(
        body=body,
        sidecar_ref=sidecar_ref,
        sidecar_path=sidecar_path,
        full_result_bytes=result_bytes,
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
