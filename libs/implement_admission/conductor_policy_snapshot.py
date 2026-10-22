"""Conductor knob snapshot — resolve, stamp, rebind (Shape A plan § State partition)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from implement_admission.impresario_policy import (
    load_impresario_policy,
    resolve_roles,
)

POLICY_KNOB_KEYS = frozenset(
    {
        "driver",
        "mechanical",
        "roles",
        "summon_mode",
        "lane",
        "policy_sha256",
        "policy_epoch",
    }
)
SCOREBOARD_SEED_KEYS = frozenset({"entry_gate", "rows", "stop_after", "sidecars", "open_forks"})


@dataclass(frozen=True, slots=True)
class ConductorKnobSnapshot:
    """Stamped conductor policy + seat knobs."""

    driver: dict[str, Any]
    mechanical: dict[str, Any]
    roles: dict[str, Any]
    lane: str
    summon_mode: str | None
    policy_sha256: str
    policy_epoch: int = 1

    def as_wire_dict(self) -> dict[str, Any]:
        return {
            "driver": dict(self.driver),
            "mechanical": dict(self.mechanical),
            "roles": dict(self.roles),
            "lane": self.lane,
            "summon_mode": self.summon_mode,
            "policy_sha256": self.policy_sha256,
            "policy_epoch": self.policy_epoch,
        }


def _canonical_knob_bytes(knobs: Mapping[str, Any]) -> bytes:
    stripped = {
        k: knobs[k]
        for k in sorted(knobs.keys())
        if k not in {"policy_sha256", "policy_epoch"}
    }
    return json.dumps(stripped, sort_keys=True, separators=(",", ":")).encode()


def stamp_policy_sha256(knobs: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_knob_bytes(knobs)).hexdigest()


def default_knobs_from_route_policy() -> ConductorKnobSnapshot:
    """Birth defaults from live ``route_policy.yaml`` impresario block (plan § default wire)."""
    pol = load_impresario_policy()
    roles = resolve_roles(pol)
    critic = roles.critic
    driver: dict[str, Any] = {
        "model": pol.driver.model,
        "effort": pol.driver.effort_rung,
    }
    if pol.driver.fast:
        driver["fast"] = pol.driver.fast
    mechanical = {"model": pol.mechanical.model, "effort": pol.mechanical.effort_rung}
    role_map: dict[str, Any] = {
        "critic": {"model": critic.model, "effort": critic.effort_rung},
        "sketch": {"effort": roles.sketch.effort_rung},
        "skeptic": {"effort": roles.skeptic.effort_rung},
        "ratify": {"effort": roles.sketch.effort_rung, "effort_when_bind_gates_wave": "max"},
        "review": {"model": roles.review.model, "effort": roles.review.effort_rung},
    }
    raw = {
        "driver": driver,
        "mechanical": mechanical,
        "roles": role_map,
        "lane": "B",
        "summon_mode": None,
    }
    sha = stamp_policy_sha256(raw)
    return ConductorKnobSnapshot(
        driver=driver,
        mechanical=mechanical,
        roles=role_map,
        lane="B",
        summon_mode=None,
        policy_sha256=sha,
        policy_epoch=1,
    )


def resolve_conductor_knobs(
    conductor_opts: Mapping[str, Any] | None,
    *,
    prior: ConductorKnobSnapshot | None = None,
    rebind_policy: bool = False,
) -> ConductorKnobSnapshot:
    base = prior or default_knobs_from_route_policy()
    if not conductor_opts:
        return base
    if rebind_policy:
        merged = _merge_knob_layers(base.as_wire_dict(), conductor_opts)
        epoch = int(conductor_opts.get("policy_epoch") or base.policy_epoch + 1)
        sha = stamp_policy_sha256(merged)
        return ConductorKnobSnapshot(
            driver=dict(merged.get("driver") or base.driver),
            mechanical=dict(merged.get("mechanical") or base.mechanical),
            roles=dict(merged.get("roles") or base.roles),
            lane=str(merged.get("lane") or base.lane),
            summon_mode=merged.get("summon_mode", base.summon_mode),
            policy_sha256=sha,
            policy_epoch=epoch,
        )
    drift_keys = POLICY_KNOB_KEYS & set(conductor_opts.keys()) - {"policy_epoch"}
    if drift_keys and prior is not None:
        return base
    merged = _merge_knob_layers(base.as_wire_dict(), conductor_opts)
    sha = stamp_policy_sha256(merged)
    return ConductorKnobSnapshot(
        driver=dict(merged.get("driver") or base.driver),
        mechanical=dict(merged.get("mechanical") or base.mechanical),
        roles=dict(merged.get("roles") or base.roles),
        lane=str(merged.get("lane") or base.lane),
        summon_mode=merged.get("summon_mode", base.summon_mode),
        policy_sha256=sha,
        policy_epoch=base.policy_epoch,
    )


def _merge_knob_layers(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key in ("driver", "mechanical", "roles", "lane", "summon_mode"):
        if key not in overlay:
            continue
        val = overlay[key]
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            merged = dict(out[key])
            merged.update(val)
            out[key] = merged
        elif val is not None:
            out[key] = val
    return out


def conductor_policy_drift(
    conductor_opts: Mapping[str, Any] | None,
    *,
    stamped: ConductorKnobSnapshot,
    rebind_policy: bool,
) -> bool:
    if rebind_policy or not conductor_opts:
        return False
    for key in POLICY_KNOB_KEYS - {"policy_epoch"}:
        if key in conductor_opts:
            incoming = conductor_opts[key]
            current = stamped.as_wire_dict().get(key)
            if incoming != current:
                return True
    return False


def selected_seat_for_hop(
    *,
    hop_seq: int,
    next_admit_verb: str | None,
    hop_reason: str | None,
) -> str:
    mechanical_verbs = frozenset(
        {"reply-fold", "child-recheck", "harvest", "fold", "park_harvest"}
    )
    verb = (next_admit_verb or hop_reason or "").strip().lower()
    if hop_seq == 0:
        return "driver"
    if verb in mechanical_verbs:
        return "mechanical"
    if verb in {"judgment", "land"}:
        return "driver"
    if verb:
        return "driver"
    return "driver"


def model_for_seat_role(snapshot: ConductorKnobSnapshot, role: str) -> str:
    if role == "mechanical":
        return str(snapshot.mechanical.get("model") or "")
    return str(snapshot.driver.get("model") or "")


def birth_seed_ignored(
    conductor_opts: Mapping[str, Any] | None,
    *,
    hop_seq: int,
    tip_present: bool = False,
) -> bool:
    if not conductor_opts:
        return False
    if not (SCOREBOARD_SEED_KEYS & set(conductor_opts.keys())):
        return False
    if hop_seq >= 1:
        return True
    return tip_present


def entry_gate_for_plan(
    *,
    hop_seq: int,
    conductor_opts: Mapping[str, Any] | None,
    tip_entry_gate: str | None,
    default_entry_gate: str,
    tip_present: bool = False,
) -> tuple[str, bool]:
    ignored = birth_seed_ignored(
        conductor_opts, hop_seq=hop_seq, tip_present=tip_present
    )
    if hop_seq >= 1 and tip_entry_gate:
        return tip_entry_gate, ignored
    if conductor_opts and conductor_opts.get("entry_gate"):
        return str(conductor_opts["entry_gate"]), ignored
    return default_entry_gate, ignored


@dataclass
class ScoreboardBirthPlan:
    rows: tuple[str, ...] | None = None
    entry_gate: str | None = None
    stop_after: str | None = None
    birth_seed_ignored: bool = False


def plan_scoreboard_birth(
    conductor_opts: Mapping[str, Any] | None,
    *,
    hop_seq: int,
    tip_present: bool,
    default_rows: tuple[str, ...],
    default_entry_gate: str,
    tip_entry_gate: str | None = None,
) -> ScoreboardBirthPlan:
    ignored = birth_seed_ignored(
        conductor_opts, hop_seq=hop_seq, tip_present=tip_present
    )
    if hop_seq >= 1 and tip_present:
        gate, _ = entry_gate_for_plan(
            hop_seq=hop_seq,
            conductor_opts=conductor_opts,
            tip_entry_gate=tip_entry_gate,
            default_entry_gate=default_entry_gate,
        )
        return ScoreboardBirthPlan(
            rows=None,
            entry_gate=gate,
            stop_after=None,
            birth_seed_ignored=ignored,
        )
    if ignored:
        return ScoreboardBirthPlan(
            rows=None,
            entry_gate=tip_entry_gate or default_entry_gate,
            stop_after=None,
            birth_seed_ignored=True,
        )
    rows_raw = (conductor_opts or {}).get("rows")
    rows: tuple[str, ...] | None = None
    if isinstance(rows_raw, list) and rows_raw:
        rows = tuple(str(r) for r in rows_raw)
    elif hop_seq == 0:
        rows = default_rows
    entry_gate = str((conductor_opts or {}).get("entry_gate") or default_entry_gate)
    stop_raw = (conductor_opts or {}).get("stop_after")
    stop_after = str(stop_raw).strip() if stop_raw else None
    return ScoreboardBirthPlan(
        rows=rows,
        entry_gate=entry_gate,
        stop_after=stop_after,
        birth_seed_ignored=False,
    )


def rebind_policy_epoch(journal_records: list[dict[str, Any]], *, bump: bool = True) -> int:
    epoch = 1
    for rec in journal_records:
        pe = rec.get("policy_epoch")
        if isinstance(pe, int) and pe > epoch:
            epoch = pe
    return epoch + 1 if bump else epoch
