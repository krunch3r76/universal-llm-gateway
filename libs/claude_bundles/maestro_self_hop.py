"""Re-export maestro self-hop knob API from ``operator_proxy_mission`` (same package)."""

from claude_bundles.operator_proxy_mission import (
    DEFAULT_MAESTRO_SELF_HOP_EFFORT,
    DEFAULT_MAESTRO_SELF_HOP_MODEL,
    ELSEWHERE_HOP_MODEL,
    MAESTRO_LANE_ID,
    MaestroSelfHopKnob,
    apply_maestro_self_hop_fields,
    load_lane_config_knob,
    maestro_self_hop_refusal_tail,
    parse_k91_from_house_rules,
    resolve_maestro_self_hop,
)

__all__ = [
    "DEFAULT_MAESTRO_SELF_HOP_EFFORT",
    "DEFAULT_MAESTRO_SELF_HOP_MODEL",
    "ELSEWHERE_HOP_MODEL",
    "MAESTRO_LANE_ID",
    "MaestroSelfHopKnob",
    "apply_maestro_self_hop_fields",
    "load_lane_config_knob",
    "maestro_self_hop_refusal_tail",
    "parse_k91_from_house_rules",
    "resolve_maestro_self_hop",
]
