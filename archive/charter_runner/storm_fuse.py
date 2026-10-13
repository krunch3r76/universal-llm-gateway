"""Storm fuse — N=3 consecutive identical park frictions ⇒ hold + quarantine (forbid §5)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from libs.charter_runner_store.db import charter_runner_data_dir

FUSE_THRESHOLD = 3
_STATE_FILENAME = "storm-fuse.json"
_CONVEYOR_ORIGIN_ATTR = "conveyor_origin"


@dataclass(frozen=True)
class FuseIdentity:
    """Same category + tip gid + admitted substrate mismatch class."""

    category: str
    tip_gid: str
    mismatch_class: str

    def key(self) -> str:
        return f"{self.category}|{self.tip_gid}|{self.mismatch_class}"


@dataclass(frozen=True)
class ParkRecordResult:
    suppressed: bool
    tripped: bool
    held: bool
    held_friction_id: int | None
    consecutive_count: int


def _state_path(*, data_dir: Path | None = None) -> Path:
    base = data_dir if data_dir is not None else charter_runner_data_dir()
    return base / _STATE_FILENAME


def _empty_state() -> dict[str, Any]:
    return {
        "consecutive_identity": None,
        "consecutive_count": 0,
        "held": False,
        "held_friction_id": None,
        "quarantine": [],
        "conveyor_origin_ids": [],
    }


def _load_state(*, data_dir: Path | None = None) -> dict[str, Any]:
    path = _state_path(data_dir=data_dir)
    if not path.is_file():
        return _empty_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_state()
    if not isinstance(raw, dict):
        return _empty_state()
    state = _empty_state()
    state.update(raw)
    state.setdefault("quarantine", [])
    state.setdefault("conveyor_origin_ids", [])
    return state


def _save_state(state: dict[str, Any], *, data_dir: Path | None = None) -> None:
    path = _state_path(data_dir=data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def fuse_identity_from_row(row: dict[str, Any]) -> FuseIdentity | None:
    """Extract park identity from friction row attrs when present."""
    attrs = row.get("attributes") or {}
    if not isinstance(attrs, dict):
        return None
    tip_gid = str(attrs.get("tip_gid") or "").strip()
    mismatch_class = str(attrs.get("mismatch_class") or "").strip()
    if not tip_gid or not mismatch_class:
        return None
    claim = str(row.get("claim") or "")
    cat_m = re.match(r"^\[([^\]]+)\]", claim)
    category = cat_m.group(1).strip() if cat_m else str(attrs.get("category") or "protocol")
    return FuseIdentity(category=category, tip_gid=tip_gid, mismatch_class=mismatch_class)


def is_conveyor_origin_friction_id(
    friction_id: int,
    *,
    data_dir: Path | None = None,
) -> bool:
    state = _load_state(data_dir=data_dir)
    key = str(friction_id)
    origin = {str(x) for x in state.get("conveyor_origin_ids") or []}
    quarantine = {str(x) for x in state.get("quarantine") or []}
    return key in origin or key in quarantine


def is_conveyor_origin_protocol(row: dict[str, Any], *, data_dir: Path | None = None) -> bool:
    attrs = row.get("attributes") or {}
    if isinstance(attrs, dict) and attrs.get(_CONVEYOR_ORIGIN_ATTR):
        return True
    try:
        friction_id = int(row["id"])
    except (KeyError, TypeError, ValueError):
        return False
    return is_conveyor_origin_friction_id(friction_id, data_dir=data_dir)


def is_quarantined(friction_id: int, *, data_dir: Path | None = None) -> bool:
    state = _load_state(data_dir=data_dir)
    return str(friction_id) in {str(x) for x in state.get("quarantine") or []}


def is_held(*, data_dir: Path | None = None) -> bool:
    return bool(_load_state(data_dir=data_dir).get("held"))


def held_friction_id_for_identity(
    identity: FuseIdentity,
    *,
    data_dir: Path | None = None,
) -> int | None:
    state = _load_state(data_dir=data_dir)
    if not state.get("held"):
        return None
    if state.get("consecutive_identity") != identity.key():
        return None
    held = state.get("held_friction_id")
    return int(held) if held is not None else None


def record_park_friction(
    identity: FuseIdentity,
    friction_id: int,
    *,
    data_dir: Path | None = None,
) -> ParkRecordResult:
    """Bump consecutive counter; dedup identical parks; trip at N=3."""
    state = _load_state(data_dir=data_dir)
    key = identity.key()
    prev_key = state.get("consecutive_identity")
    prev_count = int(state.get("consecutive_count") or 0)
    held_friction_id = state.get("held_friction_id")
    quarantine = list(state.get("quarantine") or [])
    origin_ids = list(state.get("conveyor_origin_ids") or [])
    fid_str = str(friction_id)

    if fid_str not in origin_ids:
        origin_ids.append(fid_str)

    if state.get("held") and prev_key == key and held_friction_id is not None:
        anchor = int(held_friction_id)
        if fid_str not in quarantine:
            quarantine.append(fid_str)
        state["quarantine"] = quarantine
        state["conveyor_origin_ids"] = origin_ids
        _save_state(state, data_dir=data_dir)
        return ParkRecordResult(
            suppressed=friction_id != anchor,
            tripped=False,
            held=True,
            held_friction_id=anchor,
            consecutive_count=prev_count,
        )

    if prev_key == key:
        count = prev_count + 1
        anchor = int(held_friction_id) if held_friction_id is not None else friction_id
    else:
        count = 1
        anchor = friction_id
        state["held_friction_id"] = friction_id

    tripped = count >= FUSE_THRESHOLD
    suppressed = False

    if tripped:
        state["held"] = True
        suppressed = friction_id != anchor
        for chain_id in origin_ids:
            if chain_id not in quarantine:
                quarantine.append(chain_id)
    elif state.get("held") and prev_key == key:
        suppressed = friction_id != anchor

    state["consecutive_identity"] = key
    state["consecutive_count"] = count
    state["quarantine"] = quarantine
    state["conveyor_origin_ids"] = origin_ids
    _save_state(state, data_dir=data_dir)

    return ParkRecordResult(
        suppressed=suppressed,
        tripped=tripped and count == FUSE_THRESHOLD,
        held=bool(state.get("held")),
        held_friction_id=anchor,
        consecutive_count=count,
    )


def reset_storm_fuse(*, data_dir: Path | None = None) -> None:
    """Named operator reset — clears counter, hold, and quarantine."""
    _save_state(_empty_state(), data_dir=data_dir)


__all__ = [
    "FUSE_THRESHOLD",
    "FuseIdentity",
    "ParkRecordResult",
    "fuse_identity_from_row",
    "held_friction_id_for_identity",
    "is_conveyor_origin_friction_id",
    "is_conveyor_origin_protocol",
    "is_held",
    "is_quarantined",
    "record_park_friction",
    "reset_storm_fuse",
]
