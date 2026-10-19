"""Conductor materialize context types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from implement_admission.conductor_score_journal import G_ROWS


class CortexReader(Protocol):
    """Minimal cortex read surface for todo entity_get during materialize."""

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RematerializeContext:
    """Scoped-leg keys round-tripped on conductor hop rematerialize (S4b P1)."""

    hop_seq: int | None = None
    predecessor_dispatch_id: str | None = None
    scoreboard_tip_sha: str | None = None
    scoreboard_entry_gate: str | None = None
    summoning_thread_id: str | None = None
    summoning_thread_id_unresolved: bool = False

    def effective_summoning(
        self, *, top_level_summoning_thread_id: str | None
    ) -> tuple[str | None, bool]:
        """Top-level ``dispatch_thread_id`` wins over ``generation_options``."""
        if self.summoning_thread_id_unresolved:
            return None, True
        top = str(top_level_summoning_thread_id or "").strip()
        if top:
            return top, False
        gen = str(self.summoning_thread_id or "").strip()
        if gen:
            return gen, False
        return None, False


def rematerialize_context_from_dispatch(
    *,
    hop_seq: int | None = None,
    hop_from: str | None = None,
    dispatch_thread_id: str | None = None,
    generation_options: dict[str, Any] | None = None,
) -> RematerializeContext | None:
    """Build scoped-leg context from hop ``team_dispatch`` body carriers (A5 table)."""
    opts = dict(generation_options or {})
    unresolved = bool(opts.get("summoning_thread_id_unresolved"))
    gen_summoning = str(opts.get("summoning_thread_id") or "").strip() or None
    top_summoning = str(dispatch_thread_id or "").strip() or None
    tip_sha = str(opts.get("scoreboard_tip_sha") or "").strip() or None
    entry_gate_meta = str(opts.get("scoreboard_entry_gate") or "").strip() or None
    predecessor = str(hop_from or "").strip() or None
    has_any = (
        hop_seq is not None
        or predecessor is not None
        or tip_sha is not None
        or entry_gate_meta is not None
        or gen_summoning is not None
        or top_summoning is not None
        or unresolved
    )
    if not has_any:
        return None
    return RematerializeContext(
        hop_seq=hop_seq,
        predecessor_dispatch_id=predecessor,
        scoreboard_tip_sha=tip_sha,
        scoreboard_entry_gate=entry_gate_meta,
        summoning_thread_id=gen_summoning,
        summoning_thread_id_unresolved=unresolved,
    )


@dataclass(frozen=True, slots=True)
class ConductorMaterializeContext:
    """Resolved todo attrs used to build a conductor spawn packet."""

    source_ref: str
    slug: str
    name: str
    density_triage: str | None
    derived_from: str | None
    stop_after: str | None
    entry_gate: str
    summon_mode: str
    problem: str | None
    scope: str | None
    acceptance: str | None
    fold_missing_witnesses: dict[str, str] | None = None
    summoning_thread_id: str | None = None
    summoning_thread_id_unresolved: bool = False
    rematerialize: RematerializeContext | None = None
    rows: tuple[str, ...] = G_ROWS
    row_labels: dict[str, str] | None = None
    birth_seed_ignored: bool = False
