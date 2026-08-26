"""Shared types and parsing helpers for conductor witness fold."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

_ROW_STATUS_RE = re.compile(
    r"^\|\s*(G[1-6])\s*\|[^|]*\|\s*(?P<status>[A-Za-z_()]+)",
    re.MULTILINE,
)
_G_DONE_CLAIM_RE = re.compile(
    r"^\|\s*(G[1-6])\s*\|[^|]*\|\s*DONE\b",
    re.IGNORECASE | re.MULTILINE,
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

    def nested_implement_has_commits(self, *, nest_under_dispatch_id: str) -> bool: ...


class WitnessGit(Protocol):
    """Git read surface for G6 landed-sha witness."""

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
    journal_applied: bool = False
    tip_sha: str | None = None


def row_status_in_tip(body: str, gid: str) -> str | None:
    """Return the Status cell for one G-row in a scoreboard tip."""
    for match in _ROW_STATUS_RE.finditer(body):
        if match.group(1).upper() == gid.upper():
            return match.group("status").strip().upper()
    return None


def done_rows_claimed_in_closeout(body: str) -> frozenset[str]:
    """Return G-row ids the closeout prose marks DONE."""
    return frozenset(_G_DONE_CLAIM_RE.findall(body or ""))
