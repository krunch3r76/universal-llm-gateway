"""Post-expand consume routing — tab execute vs cursor-sdk background."""

from prompt_expand_consume.router import (
    ConsumeBranch,
    ConsumeDecision,
    build_activation_envelope,
    derive_attended,
    parse_fire_hint,
    parse_operator_verb,
    parse_summon_mode_attended,
    route_consume,
    stamp_expand_provenance,
)

__all__ = [
    "ConsumeBranch",
    "ConsumeDecision",
    "build_activation_envelope",
    "derive_attended",
    "parse_fire_hint",
    "parse_operator_verb",
    "parse_summon_mode_attended",
    "route_consume",
    "stamp_expand_provenance",
]
