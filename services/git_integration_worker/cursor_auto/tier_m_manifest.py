"""Tier-M tool-op allowlist manifest — what cursor-auto may fire unattended.

Fable CDP↔cursor-auto lane consult §1 Option A: the ``execute`` contract's
admission boundary is this manifest, not a regex. A row states whether Auto may
fire the op with no operator present, and its idempotence class, so re-issue
after an ambiguous failure is safe-by-declaration rather than safe-by-hope.

**STATUS: operator ratified** — rows match
``cortex://notes/system/specs/tier-m-tool-allowlist-manifest-v0.md`` §3 and the
§7 ratification addendum (2026-07-29). ``PENDING_OPERATOR_BIND`` is ``False``;
widening any row requires an operator DISPOSITION (policy 8).

Deny-by-default: an op with no matching row is refused. Wildcard rows may only
deny (policy 9) — an ``allow`` wildcard would be an unbounded widening.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PENDING_OPERATOR_BIND = False
MANIFEST_BIND_URI = "cortex://notes/system/specs/tier-m-tool-allowlist-manifest-v0.md"
MANIFEST_BIND_NOTE = (
    "tier-M allowlist ratified at "
    f"{MANIFEST_BIND_URI} §3 + §7 (operator bind 2026-07-29)"
)

Idempotence = Literal["idempotent", "at-most-once"]


@dataclass(frozen=True, slots=True)
class ManifestRow:
    """One tier-M ``<tool>.<op>`` admission decision."""

    tool: str
    op: str
    allowed: bool
    idempotence: Idempotence
    note: str

    @property
    def tool_op(self) -> str:
        """Canonical ``<tool>.<op>`` token as authored in a DIRECTIVE."""
        return f"{self.tool}.{self.op}"

    @property
    def wildcard(self) -> bool:
        """True for a whole-tool row (``op == "*"``) — deny-only by contract."""
        return self.op == "*"


# Ratified rows — exact match before wildcard denials (policy 9).
DEFAULT_MANIFEST: tuple[ManifestRow, ...] = (
    ManifestRow(
        tool="email",
        op="pull",
        allowed=True,
        idempotence="idempotent",
        note="read-only folder pull under a bounded limit; the lane's origin case",
    ),
    ManifestRow(
        tool="email",
        op="search",
        allowed=True,
        idempotence="idempotent",
        note="read-only folder-scoped search",
    ),
    ManifestRow(
        tool="email",
        op="send",
        allowed=False,
        idempotence="at-most-once",
        note="outbound speech as the operator — human gate, never unattended",
    ),
    ManifestRow(
        tool="email",
        op="move",
        allowed=False,
        idempotence="at-most-once",
        note="mutates mailbox state with no observation path to undo it",
    ),
    ManifestRow(
        tool="email",
        op="delete",
        allowed=False,
        idempotence="at-most-once",
        note="destructive mailbox mutation",
    ),
    ManifestRow(
        tool="observability",
        op="query",
        allowed=True,
        idempotence="idempotent",
        note="read-only signal/event query",
    ),
    ManifestRow(
        tool="cortex",
        op="search",
        allowed=True,
        idempotence="idempotent",
        note="read-only knowledge retrieval",
    ),
    ManifestRow(
        tool="cortex",
        op="entity_get",
        allowed=True,
        idempotence="idempotent",
        note="read-only entity fetch",
    ),
    ManifestRow(
        tool="cortex",
        op="assert",
        allowed=False,
        idempotence="at-most-once",
        note="durable knowledge write — belongs to a contract with a closeout",
    ),
    ManifestRow(
        tool="fs",
        op="*",
        allowed=False,
        idempotence="at-most-once",
        note="repo and share writes belong to implement, not to a tool relay",
    ),
    ManifestRow(
        tool="manage",
        op="*",
        allowed=False,
        idempotence="at-most-once",
        note="substrate lifecycle (restart/sync) — operator surface only",
    ),
    ManifestRow(
        tool="pipeline",
        op="*",
        allowed=False,
        idempotence="at-most-once",
        note="spends dispatch capacity; route as a nested contract instead",
    ),
)


def assert_manifest_policy9() -> None:
    """Policy 9: wildcard rows may only deny."""
    for row in DEFAULT_MANIFEST:
        if row.wildcard and row.allowed:
            raise ValueError(
                f"policy 9 violation: wildcard row {row.tool_op} must deny"
            )


assert_manifest_policy9()


def manifest_rows() -> tuple[ManifestRow, ...]:
    """Return the active tier-M manifest rows."""
    return DEFAULT_MANIFEST


def split_tool_op(token: str) -> tuple[str, str] | None:
    """Split a ``<tool>.<op>`` token; ``None`` when the shape is wrong."""
    raw = (token or "").strip().strip("`").lower()
    if raw.count(".") != 1:
        return None
    tool, op = raw.split(".", 1)
    if not tool or not op:
        return None
    return tool, op


def lookup(token: str) -> ManifestRow | None:
    """Resolve a ``<tool>.<op>`` token to its row — exact match, then wildcard."""
    parts = split_tool_op(token)
    if parts is None:
        return None
    tool, op = parts
    for row in DEFAULT_MANIFEST:
        if row.tool == tool and row.op == op:
            return row
    for row in DEFAULT_MANIFEST:
        if row.tool == tool and row.wildcard:
            return row
    return None


def allowed_tool_ops() -> tuple[str, ...]:
    """Tokens Auto may fire unattended — the payload's self-repair vocabulary."""
    return tuple(row.tool_op for row in DEFAULT_MANIFEST if row.allowed)


def ratified_manifest_snapshot() -> tuple[tuple[str, bool, str], ...]:
    """Exact ratified row set for drift detection in tests."""
    return tuple(
        (row.tool_op, row.allowed, row.idempotence) for row in DEFAULT_MANIFEST
    )


__all__ = [
    "DEFAULT_MANIFEST",
    "MANIFEST_BIND_NOTE",
    "MANIFEST_BIND_URI",
    "PENDING_OPERATOR_BIND",
    "Idempotence",
    "ManifestRow",
    "allowed_tool_ops",
    "assert_manifest_policy9",
    "lookup",
    "manifest_rows",
    "ratified_manifest_snapshot",
    "split_tool_op",
]
