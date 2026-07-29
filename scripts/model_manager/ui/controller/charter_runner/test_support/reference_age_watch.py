"""Test-only reconstruction of retired ``age_watch.py`` JSON-clock semantics.

Not importable from production code — parity harness fixture only.
Reconstructed from cortex://notes/system/specs/charter-age-watch-belt-membership-bind.md §5
and arc 6264 §B2 intended-divergence claims (observe-gated aging; no auto-clear on FIRED).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AgeClass = Literal["tick_stall", "belt_orphan"]

TICK_STALL_MAX_AGE_S = float(os.environ.get("CHARTER_GATE_DEFER_MAX_AGE_S", str(45 * 60)))
BELT_ORPHAN_MAX_AGE_S = 600.0
BELT_ORPHAN_REPEAT_ESCALATE = 3

_STATE_FILENAME = "age-watch.json"


@dataclass(frozen=True, slots=True)
class AgeWatchResult:
    outcome: Literal["none", "act", "escalate"]
    age_s: float
    first_seen_at: float | None
    observation_count: int


@dataclass
class _Entry:
    first_seen_at: float
    observation_count: int
    last_age_s: float


def _state_path(*, data_dir: Path) -> Path:
    return data_dir / _STATE_FILENAME


def _load(data_dir: Path) -> dict[str, _Entry]:
    path = _state_path(data_dir=data_dir)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    clocks = raw.get("clocks")
    if not isinstance(clocks, dict):
        return {}
    out: dict[str, _Entry] = {}
    for key, rec in clocks.items():
        if not isinstance(rec, dict):
            continue
        first = rec.get("first_seen_at")
        if first is None:
            continue
        out[str(key)] = _Entry(
            first_seen_at=float(first),
            observation_count=int(rec.get("observation_count") or 0),
            last_age_s=float(rec.get("last_age_s") or 0.0),
        )
    return out


def _save(data_dir: Path, store: dict[str, _Entry]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _state_path(data_dir=data_dir)
    payload = {
        "clocks": {
            key: {
                "first_seen_at": entry.first_seen_at,
                "observation_count": entry.observation_count,
                "last_age_s": entry.last_age_s,
            }
            for key, entry in store.items()
        }
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _key(cls: AgeClass, key: str) -> str:
    return f"{cls}:{key}"


def _refuse_root(key: str) -> tuple[str, bool]:
    if key.endswith(":refuse"):
        return key[: -len(":refuse")], True
    return key, False


def observation_count(
    cls: AgeClass,
    key: str,
    *,
    data_dir: Path,
) -> int:
    store = _load(data_dir)
    entry = store.get(_key(cls, key))
    return entry.observation_count if entry is not None else 0


def first_seen_at(
    cls: AgeClass,
    key: str,
    *,
    data_dir: Path,
) -> float | None:
    store = _load(data_dir)
    entry = store.get(_key(cls, key))
    return entry.first_seen_at if entry is not None else None


def age_s(
    cls: AgeClass,
    key: str,
    *,
    birth: float | None = None,
    now: float | None = None,
    data_dir: Path,
) -> float:
    """Age frozen between ``observe`` calls — inert across tick-down gaps."""
    if birth is not None:
        assert now is not None
        return max(0.0, now - birth)
    store = _load(data_dir)
    entry = store.get(_key(cls, key))
    if entry is None:
        return 0.0
    return entry.last_age_s


def clear(cls: AgeClass, key: str, *, data_dir: Path) -> None:
    store = _load(data_dir)
    store.pop(_key(cls, key), None)
    _save(data_dir, store)


def observe(
    cls: AgeClass,
    key: str,
    *,
    present: bool,
    birth: float | None = None,
    now: float,
    data_dir: Path,
) -> AgeWatchResult:
    """Observe-gated birth; age advances only on observation (§B2 divergence driver)."""
    if not present:
        clear(cls, key, data_dir=data_dir)
        return AgeWatchResult(
            outcome="none",
            age_s=0.0,
            first_seen_at=None,
            observation_count=0,
        )

    store = _load(data_dir)
    storage_key = _key(cls, key)
    entry = store.get(storage_key)
    if entry is None:
        first = float(birth) if birth is not None else now
        count = 1
    else:
        first = entry.first_seen_at
        count = entry.observation_count + 1

    current_age = max(0.0, now - first)
    store[storage_key] = _Entry(
        first_seen_at=first,
        observation_count=count,
        last_age_s=current_age,
    )
    _save(data_dir, store)

    if cls == "belt_orphan":
        if count >= BELT_ORPHAN_REPEAT_ESCALATE:
            outcome: Literal["none", "act", "escalate"] = "escalate"
        elif current_age >= BELT_ORPHAN_MAX_AGE_S:
            outcome = "act"
        else:
            outcome = "none"
    elif current_age >= TICK_STALL_MAX_AGE_S:
        outcome = "escalate"
    else:
        outcome = "none"

    return AgeWatchResult(
        outcome=outcome,
        age_s=current_age,
        first_seen_at=first,
        observation_count=count,
    )


def seed_first_seen(
    cls: AgeClass,
    key: str,
    first_seen_at_ts: float,
    *,
    observation_count: int = 1,
    now: float,
    data_dir: Path,
) -> None:
    age = max(0.0, now - first_seen_at_ts)
    store = _load(data_dir)
    store[_key(cls, key)] = _Entry(
        first_seen_at=float(first_seen_at_ts),
        observation_count=int(observation_count),
        last_age_s=age,
    )
    _save(data_dir, store)
