"""Shared types and parsing helpers for conductor witness fold."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from implement_admission.conductor_score_table import (
    cell,
    row_id_in,
    status_index,
    status_token,
    stops_index,
)

STOPS_BLOCK_TOKENS: frozenset[str] = frozenset(
    {"ROW_PINNED", "CONSULT_PENDING", "HOLD_MERGE", "OPERATOR_GATE"}
)


class WitnessCortex(Protocol):
    """Cortex read surface for conductor row witnesses."""

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]: ...

    def list_relationships(
        self,
        entity_id: str,
        *,
        type_id: str | None = None,
    ) -> list[dict[str, Any]]: ...


class WitnessBus(Protocol):
    """Bus read surface for attended G5 resurface witness."""

    def has_score_resurface_after(
        self,
        *,
        thread_id: str,
        after_written_at: str | None,
    ) -> bool: ...


class WitnessNestedImplement(Protocol):
    """Ledger read surface for headless G5 nested-implement commit witness."""

    def nested_implement_has_commits(self, *, nest_under_dispatch_id: str) -> bool: ...


class WitnessGit(Protocol):
    """Git read surface for G7 landed-sha witness."""

    def is_ancestor(self, commit: str, ref: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class Witness:
    """One hung witness backing a rendered DONE cell."""

    row: str
    source: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class FoldDeps:
    """Readers required to fold a conductor scoreboard tip."""

    cortex: WitnessCortex
    bus: WitnessBus | None = None
    nested_implement: WitnessNestedImplement | None = None
    git: WitnessGit | None = None
    source_ref: str | None = None
    summon_mode: str | None = None
    summoning_thread_id: str | None = None
    repo: Path | None = None


@dataclass(frozen=True, slots=True)
class FoldResult:
    """Outcome of rendering tip Status cells from witnesses."""

    slug: str
    raw_body: str
    folded_body: str
    row_status: dict[str, str]
    witnesses: dict[str, Witness | None]
    witnessed_done: frozenset[str]
    rows_claimed: frozenset[str]
    entry_gate: str
    missing_witnesses: dict[str, str] = field(default_factory=dict)
    blocked_rows: dict[str, str] = field(default_factory=dict)
    journal_applied: bool = False
    tip_sha: str | None = None


def row_status_in_tip(body: str, gid: str) -> str | None:
    """Return the Status cell for one G-row in a scoreboard tip."""
    column = status_index(body)
    for line in (body or "").splitlines():
        if row_id_in(line) == gid.upper():
            return status_token(cell(line, column))
    return None


def done_rows_claimed_in_closeout(body: str) -> frozenset[str]:
    """Return G-row ids the closeout prose marks DONE."""
    column = status_index(body)
    claimed = set()
    for line in (body or "").splitlines():
        row_id = row_id_in(line)
        if row_id and status_token(cell(line, column)) == "DONE":
            claimed.add(row_id)
    return frozenset(claimed)


def stops_block_reason(tip_body: str, row_id: str) -> str | None:
    """Return a Stops-column block token for one scoreboard row, if present.

    A block is an exact whole-word token from ``STOPS_BLOCK_TOKENS``
    (case-sensitive). Prose without a token is not a block.
    """
    column = stops_index(tip_body)
    for line in (tip_body or "").splitlines():
        if row_id_in(line) != row_id.upper():
            continue
        stops = cell(line, column)
        if not stops:
            return None
        for token in STOPS_BLOCK_TOKENS:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", stops):
                return token
        return None
    return None
