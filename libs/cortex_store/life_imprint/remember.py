"""Remember orchestration — propose∘commit compression for commit-eligible patches."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any

from ..db import cortex_conn
from .apply import ImprintCommitError, commit_imprint_proposal
from .op_plan import build_op_plan, normalize_patch
from .proposal_store import (
    IMPRINT_REMEMBER_DEDUPE_SECONDS,
    create_proposal,
    find_committed_by_patch_hash,
    find_open_by_patch_hash,
    patch_sha256,
)
from .registry import LifeVocabRegistry, load_registry
from .shape_check import ShapeReject, shape_check_patch

_STATUS_PREVIEW = "preview"
_STATUS_SUCCESS = "success"

_patch_locks: dict[str, threading.Lock] = {}
_patch_locks_guard = threading.Lock()


def _patch_lock(patch_hash: str) -> threading.Lock:
    with _patch_locks_guard:
        lock = _patch_locks.get(patch_hash)
        if lock is None:
            lock = threading.Lock()
            _patch_locks[patch_hash] = lock
        return lock


@dataclass(frozen=True)
class RememberPreviewResult:
    normalized_patch: dict[str, Any]
    op_plan: list[dict[str, Any]]
    rejects: list[ShapeReject]
    candidates: list[dict[str, Any]]
    context: str
    status: str = _STATUS_PREVIEW


@dataclass(frozen=True)
class RememberSuccessResult:
    proposal_id: str
    applied: list[dict[str, Any]]
    normalized_patch: dict[str, Any]
    context: str
    deduped: bool
    status: str = _STATUS_SUCCESS


def _is_commit_eligible(
    *,
    rejects: list[ShapeReject],
    candidates: list[dict[str, Any]],
    op_plan: list[dict[str, Any]],
) -> bool:
    return not rejects and not candidates and bool(op_plan)


def _deduped_success(row: dict[str, Any], context: str) -> RememberSuccessResult:
    applied = row.get("applied_result") or []
    return RememberSuccessResult(
        proposal_id=str(row["id"]),
        applied=applied,
        normalized_patch=row.get("normalized_patch") or {},
        context=context,
        deduped=True,
    )


def _run_propose_pipeline(
    patch: dict[str, Any],
    registry: LifeVocabRegistry,
) -> RememberPreviewResult | tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    normalized = normalize_patch(patch, registry)
    rejects = shape_check_patch(patch, registry)
    if rejects:
        return RememberPreviewResult(
            normalized_patch=normalized,
            op_plan=[],
            rejects=rejects,
            candidates=[],
            context=registry.context_id,
        )

    conn = cortex_conn()
    try:
        op_plan, candidates = build_op_plan(patch, registry, conn)
    finally:
        conn.close()

    if not _is_commit_eligible(
        rejects=[], candidates=candidates, op_plan=op_plan
    ):
        return RememberPreviewResult(
            normalized_patch=normalized,
            op_plan=op_plan,
            rejects=[],
            candidates=candidates,
            context=registry.context_id,
        )

    return normalized, op_plan, candidates


def _resolve_inflight_proposal(
    patch_hash: str,
    context: str,
) -> RememberSuccessResult | str:
    """Collapse concurrent same-patch remember attempts onto one proposal."""
    for attempt in range(10):
        committed = find_committed_by_patch_hash(
            patch_hash, IMPRINT_REMEMBER_DEDUPE_SECONDS
        )
        if committed is not None:
            return _deduped_success(committed, context)

        open_row = find_open_by_patch_hash(patch_hash)
        if open_row is not None:
            return str(open_row["id"])

        if attempt < 9:
            time.sleep(0.005)

    raise sqlite3.IntegrityError("in-flight proposal collision")


def run_remember(
    patch: dict[str, Any],
    *,
    registry: LifeVocabRegistry | None = None,
) -> RememberPreviewResult | RememberSuccessResult:
    """Validate like propose; auto-commit when commit-eligible."""
    reg = registry or load_registry()
    pipeline = _run_propose_pipeline(patch, reg)
    if isinstance(pipeline, RememberPreviewResult):
        return pipeline

    normalized, op_plan, _candidates = pipeline
    patch_hash = patch_sha256(normalized)

    with _patch_lock(patch_hash):
        committed = find_committed_by_patch_hash(
            patch_hash, IMPRINT_REMEMBER_DEDUPE_SECONDS
        )
        if committed is not None:
            return _deduped_success(committed, reg.context_id)

        proposal_id: str | None = None
        try:
            proposal_id = create_proposal(
                normalized_patch=normalized,
                op_plan=op_plan,
                rejects=[],
                candidates=[],
                patch_hash=patch_hash,
            )
        except sqlite3.IntegrityError:
            resolved = _resolve_inflight_proposal(patch_hash, reg.context_id)
            if isinstance(resolved, RememberSuccessResult):
                return resolved
            proposal_id = resolved

        assert proposal_id is not None
        try:
            result = commit_imprint_proposal(proposal_id)
        except ImprintCommitError as exc:
            if exc.code == "proposal_already_committed":
                committed = find_committed_by_patch_hash(
                    patch_hash, IMPRINT_REMEMBER_DEDUPE_SECONDS
                )
                if committed is not None:
                    return _deduped_success(committed, reg.context_id)
            exc.data = {**exc.data, "proposal_id": proposal_id}
            raise

        return RememberSuccessResult(
            proposal_id=proposal_id,
            applied=result.get("applied") or [],
            normalized_patch=normalized,
            context=reg.context_id,
            deduped=False,
        )


__all__ = [
    "RememberPreviewResult",
    "RememberSuccessResult",
    "run_remember",
]
