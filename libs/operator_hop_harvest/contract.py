"""Typed shapes for operator-hop-harvest R6 output (documentation + tests)."""

from __future__ import annotations

from typing import TypedDict


class ThreadRef(TypedDict, total=False):
    id: str
    slug: str


class ConductorView(TypedDict, total=False):
    dispatch_id: str
    hop_seq: int | None
    status: str | None
    work_outcome: str | None
    degraded_reason: str | None
    stop_tokens: list[str]
    next_admit: str | None
    branch: str | None
    head_sha: str | None
    commits_ahead: int | None
    usage: dict[str, object]
    closeout_turn: int
    closeout_uri: str | None


class ScoreboardView(TypedDict, total=False):
    uri: str
    sha256: str
    entry_gate: str | None
    rows: list[dict[str, object]]
    next_admit_tip: str | None


class WaitView(TypedDict, total=False):
    open: bool
    kind: str
    since: int | None
    asks: list[str]
    sidecar_uri: str | None


class OperatorHopView(TypedDict, total=False):
    worker_thread: ThreadRef
    summoning_thread: ThreadRef
    conductor: ConductorView
    scoreboard: ScoreboardView
    wait: WaitView
    job: dict[str, object] | None
    harvest_recipe: dict[str, object]
    continuation: dict[str, object]
    next_admit_divergent: bool
