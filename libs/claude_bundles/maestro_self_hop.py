"""Maestro operator-lane self-hop model knob (K91 maestro house; K22 elsewhere)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_MAESTRO_SELF_HOP_MODEL = "cdp/fable-5.1"
DEFAULT_MAESTRO_SELF_HOP_EFFORT = "medium"
ELSEWHERE_HOP_MODEL = "cdp/opus-5.5-extra"

_K91_KNOB_RE = re.compile(
    r"^\s*-\s*K91\b[^\n]*?"
    r"desired_model=(?P<model>\S+)\s+desired_effort=(?P<effort>\S+)",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class MaestroSelfHopKnob:
    desired_model: str
    desired_effort: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _lane_config_path(lane_id: str) -> Path:
    return _repo_root() / "config" / "lanes" / f"{lane_id}.yaml"


def load_lane_config_knob(lane_id: str) -> MaestroSelfHopKnob | None:
    path = _lane_config_path(lane_id)
    if not path.is_file():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    block = raw.get("maestro_self_hop") or {}
    model = (block.get("desired_model") or "").strip()
    effort = (block.get("desired_effort") or "").strip()
    if not model or not effort:
        return None
    return MaestroSelfHopKnob(desired_model=model, desired_effort=effort)


def parse_k91_from_house_rules(text: str | None) -> MaestroSelfHopKnob | None:
    if not text:
        return None
    match = _K91_KNOB_RE.search(text)
    if not match:
        return None
    return MaestroSelfHopKnob(
        desired_model=match.group("model"),
        desired_effort=match.group("effort"),
    )


def resolve_maestro_self_hop(
    *,
    house_rules_text: str | None = None,
    lane_id: str = "12286",
) -> MaestroSelfHopKnob:
    """One knob read: K91 house-rules row, then lane config, then K91 defaults."""
    parsed = parse_k91_from_house_rules(house_rules_text)
    if parsed is not None:
        return parsed
    from_config = load_lane_config_knob(lane_id)
    if from_config is not None:
        return from_config
    return MaestroSelfHopKnob(
        desired_model=DEFAULT_MAESTRO_SELF_HOP_MODEL,
        desired_effort=DEFAULT_MAESTRO_SELF_HOP_EFFORT,
    )


def maestro_self_hop_refusal_tail(knob: MaestroSelfHopKnob) -> str:
    return (
        f"maestro house: desired_model={knob.desired_model} "
        f"desired_effort={knob.desired_effort} (K91, FACT 12286#4800); "
        f"elsewhere {ELSEWHERE_HOP_MODEL} (K22)"
    )


def apply_maestro_self_hop_fields(
    fields: dict[str, str],
    *,
    house_rules_text: str | None,
    lane_id: str,
) -> MaestroSelfHopKnob:
    knob = resolve_maestro_self_hop(
        house_rules_text=house_rules_text,
        lane_id=lane_id,
    )
    fields["maestro_self_hop_model"] = knob.desired_model
    fields["maestro_self_hop_effort"] = knob.desired_effort
    fields["maestro_self_hop_refusal_tail"] = maestro_self_hop_refusal_tail(knob)
    return knob


__all__ = [
    "DEFAULT_MAESTRO_SELF_HOP_EFFORT",
    "DEFAULT_MAESTRO_SELF_HOP_MODEL",
    "ELSEWHERE_HOP_MODEL",
    "MaestroSelfHopKnob",
    "apply_maestro_self_hop_fields",
    "load_lane_config_knob",
    "maestro_self_hop_refusal_tail",
    "parse_k91_from_house_rules",
    "resolve_maestro_self_hop",
]
