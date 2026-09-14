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
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._card_patch import apply_fold_summary_to_card, derive_settled_live_next

logger = logging.getLogger(__name__)

_REPO = Path("/mnt/torus/projects/universal-llm-gateway")
def _format_scoreboard_pin(uri: str, sha256: str | None) -> str:
    short = (sha256 or "")[:8]
    return f"Scoreboard: {uri} · sha256:{short}" if short else f"Scoreboard: {uri}"


def _fold_row_lines(fold_body: str) -> list[str]:
    lines: list[str] = []
    in_gated = False
    for line in (fold_body or "").splitlines():
        if line.startswith("## Gated deliverables"):
            in_gated = True
            continue
        if line.startswith("## "):
            in_gated = False
        if in_gated and line.lstrip().startswith("| G"):
            lines.append(line.strip())
    return lines


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
        return {
            "folded": False,
            "family": "charter",
            "reason": "validate_only",
            "scoreboard_uri": ref.uri,
            "scoreboard_sha256": ref.sha256,
            "scoreboard_pin": pin,
            "card_written": False,
            "card_reason": "card_derivation_unavailable",
        }

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
        fold = fold_scoreboard(
            slug,
            deps=deps,
            files_root=files_root,
            write_journal=True,
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

    if fold.journal_applied is False and fold.folded_body != fold.raw_body:
        return {
            "folded": False,
            "family": "conductor",
            "reason": "journal_rejected",
            "scoreboard_uri": ref.uri,
            "scoreboard_sha256": fold.tip_sha,
            "scoreboard_pin": pin,
        }

    tip_sha = fold.tip_sha or tip_sha256(fold.folded_body)
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
        "folded": True,
        "family": "conductor",
        "reason": "witness_fold" if fold.journal_applied else "fold_idempotent",
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


class ContinuityCheckpointTailMechanicalHandler(BaseHandler):
    """Resolve scoreboard family; fold conductor boards; validate charter boards."""

    step_type = "continuity_checkpoint_tail_mechanical_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options: dict[str, Any] = dict(getattr(context, "options", {}) or {})
        options["execution_id"] = str(context.execution_id or "")
        thread = str(options.get("thread") or context.dispatch_thread_id or "")
        tags = list(options.get("tags") or [])
        tip_body = await asyncio.to_thread(_tip_checkpoint_body_sync, thread)
        files_root = None
        root_env = options.get("files_root")
        if root_env:
            files_root = Path(str(root_env))
        payload = run_tail_mechanical(
            thread=thread,
            options=options,
            tip_body=tip_body,
            thread_tags=tags,
            files_root=files_root,
        )
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
