"""CHECKPOINT tail — family-gated scoreboard fold and card derivation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, override

from agent_bus_store.scoreboard_ref import resolve_scoreboard_ref
from implement_admission.conductor_score_journal import tip_sha256
from implement_admission.conductor_witness import fold_scoreboard
from implement_admission.conductor_witness_defaults import (
    DefaultWitnessCortex,
    fold_deps_for_admit,
)
from implement_admission.scoreboard_genre import (
    detect_genre,
    ordered_row_ids,
    project_row_status,
)
from implement_admission.scoreboard_genre import (
    fold_row_lines as genre_fold_row_lines,
)
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._card_patch import apply_fold_summary_to_card, derive_settled_live_next

logger = logging.getLogger(__name__)

_REPO = Path("/mnt/torus/projects/universal-llm-gateway")
def _format_scoreboard_pin(uri: str, sha256: str | None) -> str:
    short = (sha256 or "")[:8]
    return f"Scoreboard: {uri} · sha256:{short}" if short else f"Scoreboard: {uri}"


def _fold_row_lines(fold_body: str) -> list[str]:
    return genre_fold_row_lines(fold_body)


def _scoreboard_files_root(files_root: Path | None) -> Path:
    if files_root is not None:
        return files_root
    import os

    root_env = os.environ.get("CORTEX_FILES_ROOT")
    if root_env:
        return Path(root_env)
    return _REPO


def _read_scoreboard_body(uri: str, *, files_root: Path | None) -> str | None:
    if not uri.startswith("cortex://"):
        return None
    path = _scoreboard_files_root(files_root) / uri.removeprefix("cortex://")
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _project_charter_board(
    *,
    thread: str,
    ref_uri: str,
    ref_sha: str,
    pin: str,
    body: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Genre-detected read-only projection for charter-family tips."""
    genre = detect_genre(body)
    if genre == "none":
        return {
            "folded": False,
            "family": "charter",
            "reason": "validate_only",
            "genre": genre,
            "scoreboard_uri": ref_uri,
            "scoreboard_sha256": ref_sha,
            "scoreboard_pin": pin,
            "card_written": False,
            "card_reason": "card_derivation_unavailable",
        }

    row_status = project_row_status(body)
    rows = ordered_row_ids(body)
    if not rows:
        return {
            "folded": False,
            "family": "charter",
            "reason": "validate_only",
            "genre": genre,
            "scoreboard_uri": ref_uri,
            "scoreboard_sha256": ref_sha,
            "scoreboard_pin": pin,
            "card_written": False,
            "card_reason": "card_derivation_unavailable",
        }

    summary = derive_settled_live_next(row_status, rows)
    card_written, _card_uri, card_reason = apply_fold_summary_to_card(
        thread=thread,
        settled=summary["settled"],
        live=summary["live"],
        next_row=summary["next"],
    )
    from continuity_tape.events_checkpoint import (
        stargate_continuity_checkpoint_tail_folded,
    )

    stargate_continuity_checkpoint_tail_folded(
        execution_id=str(options.get("execution_id") or ""),
        thread=thread,
        family="charter",
        slug=ref_uri.rsplit("/", 1)[-1].removesuffix("-scoreboard.md"),
        journal_applied=False,
    )
    live_row = summary["live"]
    entry_gate = live_row if live_row != "none" else (rows[-1] if rows else "")
    return {
        "folded": False,
        "family": "charter",
        "reason": "projection_only",
        "genre": genre,
        "scoreboard_uri": ref_uri,
        "scoreboard_sha256": ref_sha,
        "scoreboard_pin": pin,
        "fold_row_lines": genre_fold_row_lines(body),
        "row_status": row_status,
        "entry_gate": entry_gate,
        "journal_applied": False,
        "card_written": card_written,
        "card_reason": card_reason if card_written else "card_derivation_unavailable",
        "settled_live_next": summary,
    }


def _skipped_payload(*, reason: str, family: str | None = None) -> dict[str, Any]:
    from agent_bus_store.events.publisher import emit

    payload: dict[str, Any] = {"folded": False, "reason": reason}
    if family:
        payload["family"] = family
    emit("checkpoint.tail_mechanical.skipped", payload)
    return payload


def run_tail_mechanical(
    *,
    thread: str,
    options: dict[str, Any],
    tip_body: str,
    thread_tags: list[str],
    files_root: Path | None = None,
) -> dict[str, Any]:
    """Family-gated fold/validate — never raises (B1-5/B1-6)."""
    ref = resolve_scoreboard_ref(
        options=options,
        tip_body=tip_body,
        thread_tags=thread_tags,
        files_root=files_root,
    )
    if ref is None:
        return _skipped_payload(reason="scoreboard_unresolved")

    pin = _format_scoreboard_pin(ref.uri, ref.sha256)

    if ref.family == "charter":
        if ref.sha256 is None:
            return {
                **_skipped_payload(reason="scoreboard_unreadable", family="charter"),
                "scoreboard_uri": ref.uri,
            }
        body = _read_scoreboard_body(ref.uri, files_root=files_root)
        if body is None:
            return {
                **_skipped_payload(reason="scoreboard_unreadable", family="charter"),
                "scoreboard_uri": ref.uri,
            }
        return _project_charter_board(
            thread=thread,
            ref_uri=ref.uri,
            ref_sha=ref.sha256,
            pin=pin,
            body=body,
            options=options,
        )

    slug = ref.slug
    if not slug:
        return _skipped_payload(reason="scoreboard_unresolved", family="conductor")

    source_ref = f"todo:{slug}"
    try:
        deps = fold_deps_for_admit(
            source_ref,
            cortex=DefaultWitnessCortex(),
            repo=_REPO,
        )
        # Projection only. The witness fold rewrites operator-recorded DONE
        # gates to CLAIMED; CHECKPOINT fires far more often and on more roots
        # than a conductor hop, so the tail reads and reports, never writes.
        fold = fold_scoreboard(
            slug,
            deps=deps,
            files_root=files_root,
            write_journal=False,
        )
    except Exception as exc:  # noqa: BLE001 — checkpoint must not fail
        logger.warning("tail_mechanical fold failed slug=%s err=%s", slug, exc)
        return {
            **_skipped_payload(reason="scoreboard_unreadable", family="conductor"),
            "scoreboard_uri": ref.uri,
            "error": str(exc)[:200],
        }

    if fold is None:
        return {
            **_skipped_payload(reason="entity_missing", family="conductor"),
            "scoreboard_uri": ref.uri,
        }

    # Projection-only: an unapplied journal is the expected outcome here, so it
    # is not a rejection. fold.tip_sha describes the projected body, which was
    # never written — a pin must name what a reader will actually find on disk.
    tip_sha = tip_sha256(fold.raw_body)
    pin = _format_scoreboard_pin(ref.uri, tip_sha)
    summary = derive_settled_live_next(fold.row_status, tuple(fold.row_status.keys()))
    card_written, _card_uri, card_reason = apply_fold_summary_to_card(
        thread=thread,
        settled=summary["settled"],
        live=summary["live"],
        next_row=summary["next"],
    )
    from continuity_tape.events_checkpoint import (
        stargate_continuity_checkpoint_tail_folded,
    )

    stargate_continuity_checkpoint_tail_folded(
        execution_id=str(options.get("execution_id") or ""),
        thread=thread,
        family="conductor",
        slug=slug,
        journal_applied=bool(fold.journal_applied),
    )
    return {
        "folded": False,
        "family": "conductor",
        "reason": "projection_only",
        "scoreboard_uri": ref.uri,
        "scoreboard_sha256": tip_sha,
        "scoreboard_pin": pin,
        "fold_row_lines": _fold_row_lines(fold.folded_body),
        "row_status": fold.row_status,
        "entry_gate": fold.entry_gate,
        "journal_applied": fold.journal_applied,
        "card_written": card_written,
        "card_reason": card_reason if card_written else "card_derivation_unavailable",
        "settled_live_next": summary,
    }


def _tip_checkpoint_body_sync(thread: str) -> str:
    from agent_bus_store.resume_envelope import _tip_checkpoint_body

    _turn, body = _tip_checkpoint_body(thread)
    return body


def _safe_tip_checkpoint_body(thread: str) -> tuple[str, str | None]:
    """Tip body plus a skip reason — the store is unreachable from some hosts.

    Returns ("", reason) rather than raising: losing the tip-body resolution
    hint must degrade the fold to a skip, never fail the caller's CHECKPOINT.
    """
    try:
        return _tip_checkpoint_body_sync(thread), None
    except Exception as exc:  # noqa: BLE001 — checkpoint must not fail
        logger.warning("tail_mechanical tip read failed thread=%s err=%s", thread, exc)
        return "", f"tip_body_unavailable: {exc}"[:200]


def _safe_thread_tags(thread: str) -> list[str]:
    """Thread tags from the store — the relay does not forward them in options.

    Without this the scoreboard: tag precedence steps are unreachable in
    production, leaving tip-body and explicit option as the only paths.
    """
    try:
        from agent_bus_store.db.connection import connect
        from agent_bus_store.db.threads import _load_thread_tags

        with connect() as conn:
            return _load_thread_tags(conn, [thread]).get(thread, [])
    except Exception as exc:  # noqa: BLE001 — checkpoint must not fail
        logger.warning("tail_mechanical tag read failed thread=%s err=%s", thread, exc)
        return []


class ContinuityCheckpointTailMechanicalHandler(BaseHandler):
    """Resolve scoreboard family; fold conductor boards; validate charter boards."""

    step_type = "continuity_checkpoint_tail_mechanical_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options: dict[str, Any] = dict(getattr(context, "options", {}) or {})
        options["execution_id"] = str(context.execution_id or "")
        thread = str(options.get("thread") or context.dispatch_thread_id or "")
        tags = list(options.get("tags") or [])
        if not tags:
            tags = await asyncio.to_thread(_safe_thread_tags, thread)
        tip_body, tip_error = await asyncio.to_thread(
            _safe_tip_checkpoint_body, thread
        )
        files_root = None
        root_env = options.get("files_root")
        if root_env:
            files_root = Path(str(root_env))
        # This step is advisory: no failure inside it may sink the CHECKPOINT.
        try:
            payload = run_tail_mechanical(
                thread=thread,
                options=options,
                tip_body=tip_body,
                thread_tags=tags,
                files_root=files_root,
            )
        except Exception as exc:  # noqa: BLE001 — checkpoint must not fail
            logger.warning("tail_mechanical failed thread=%s err=%s", thread, exc)
            payload = {"folded": False, "reason": "tail_error", "error": str(exc)[:200]}
        if tip_error:
            payload["tip_error"] = tip_error
        return StepOutput(raw=json.dumps(payload, default=str), json=payload)


def scoreboard_sha_on_disk(uri: str, *, files_root: Path) -> str:
    """Return sha256 of scoreboard file — test helper for charter immutability."""
    path = files_root / uri.removeprefix("cortex://")
    text = path.read_text(encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "ContinuityCheckpointTailMechanicalHandler",
    "run_tail_mechanical",
    "scoreboard_sha_on_disk",
]
