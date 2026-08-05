"""Declared concurrency posture for cursor-sdk write admits (Rank-1 honesty)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from services.git_integration_worker.cursor_sdk_workspace import Lane

Posture = Literal["sole_a", "multi_a_operator", "multi_b", "nest_child"]
GateLane = Literal["standard", "operator"]

_POSTURE_KEY = "concurrency_posture"
_ISOLATION_MATERIALIZED_KEY = "isolation_materialized"


def operator_multi_a_enabled() -> bool:
    """Explicit switch for operator Lane-A multi-lease (default OFF — inert land)."""
    raw = os.environ.get("CURSOR_SDK_OPERATOR_MULTI_A_ENABLED", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def refuse_b_without_worktree_enabled() -> bool:
    """Leg-2 refusal for Lane-B admits lacking materialized worktree (default OFF)."""
    raw = os.environ.get("CURSOR_SDK_REFUSE_B_WITHOUT_WORKTREE", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def derive_concurrency_posture(
    *,
    admit_lane: Lane,
    gate_lane: GateLane,
    read_only: bool,
    nest_under: str | None,
    worktree_path: Path | None,
) -> Posture | None:
    """Derive stamped posture; None when lease-exempt (read_only)."""
    if read_only:
        return None
    if nest_under:
        return "nest_child"
    if admit_lane == "B":
        if worktree_path is None or not worktree_path.is_dir():
            return None
        return "multi_b"
    if gate_lane == "operator" and operator_multi_a_enabled():
        return "multi_a_operator"
    return "sole_a"


def write_lease_slot_limit(
    *,
    admit_lane: Lane,
    posture: Posture | None,
) -> int:
    """Grantable concurrent write leases for this admit binding."""
    if posture == "nest_child":
        return 1
    if admit_lane == "A":
        if posture == "multi_a_operator" and operator_multi_a_enabled():
            raw = os.environ.get("CURSOR_SDK_OPERATOR_DISPATCH_CONCURRENCY", "3")
            return max(1, int(raw))
        return 1
    std = max(1, int(os.environ.get("CURSOR_SDK_DISPATCH_CONCURRENCY", "1")))
    op = max(1, int(os.environ.get("CURSOR_SDK_OPERATOR_DISPATCH_CONCURRENCY", "3")))
    return std + op


def posture_from_record_json(record_json: str | None) -> Posture | None:
    if not record_json:
        return None
    try:
        data = json.loads(record_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get(_POSTURE_KEY)
    if raw in ("sole_a", "multi_a_operator", "multi_b", "nest_child"):
        return raw  # type: ignore[return-value]
    return None


def stamp_posture_on_record_json(record_json: str, posture: Posture) -> str:
    data = json.loads(record_json) if record_json else {}
    if not isinstance(data, dict):
        data = {}
    data[_POSTURE_KEY] = posture
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def b_worktree_materialized(
    *,
    admit_lane: Lane,
    lease_key: str | None,
    source_repo: str,
) -> bool:
    """Whether Lane-B worktree binding is satisfied at admit.

    Lane-B: ``True`` only when ``lease_key`` differs from ``source_repo`` and
    exists on disk; ``False`` when nominal B lacks a materialized worktree.

    Non-B (Lane-A): always ``True`` — vacuous pass ("B worktree gate not
    applicable"), **not** a claim that an isolated worktree was materialized.
    Pair with ``lane`` / ``reported_admit_lane`` to interpret stamped rows.
    """
    if admit_lane != "B":
        return True
    if not lease_key or lease_key == source_repo:
        return False
    return Path(lease_key).is_dir()


def reported_admit_lane(
    *,
    selected_lane: Lane,
    lease_key: str | None,
    source_repo: str,
) -> Lane:
    """Ledger/closeout lane label — B only when isolation is materialized on disk."""
    if selected_lane == "B" and b_worktree_materialized(
        admit_lane="B",
        lease_key=lease_key,
        source_repo=source_repo,
    ):
        return "B"
    return "A"


def stamp_isolation_on_record_json(
    record_json: str,
    *,
    isolation_materialized: bool,
) -> str:
    """Persist admit-time ``isolation_materialized`` on durable ``record_json``."""
    data = json.loads(record_json) if record_json else {}
    if not isinstance(data, dict):
        data = {}
    data[_ISOLATION_MATERIALIZED_KEY] = isolation_materialized
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def isolation_materialized_from_record_json(record_json: str | None) -> bool | None:
    """Return stamped ``isolation_materialized`` when present; else ``None``."""
    if not record_json:
        return None
    try:
        data = json.loads(record_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get(_ISOLATION_MATERIALIZED_KEY)
    if isinstance(raw, bool):
        return raw
    return None


__all__ = [
    "GateLane",
    "Posture",
    "b_worktree_materialized",
    "derive_concurrency_posture",
    "isolation_materialized_from_record_json",
    "operator_multi_a_enabled",
    "posture_from_record_json",
    "refuse_b_without_worktree_enabled",
    "reported_admit_lane",
    "stamp_isolation_on_record_json",
    "stamp_posture_on_record_json",
    "write_lease_slot_limit",
]
