"""DIRECTIVE prose fields consumed outside the AutoJob envelope.

Envelope parity compares authored keys to ``AutoJob`` bindable fields.
Operator-proxy / cursor-auto DIRECTIVE bodies also carry a standing prose
vocabulary (arc, vision, files_expected, …) that other scopes consume.
Those keys are recognised-but-out-of-envelope — not unknown tokens.

Derived from the parsers in ``directive.py`` plus the Tier-M DIRECTIVE
template in ``claude_bundles.operator_proxy_tier_m`` so a new parsed
field joins this set by being added next to its consumer, not here as a
third snapshot. Template-only keys that have no parser still belong:
they are authored contract, just not envelope-bindable.
"""

from __future__ import annotations

# Keys ``directive.py`` parses or gates on (minus envelope / _GATE_KEYS).
_DIRECTIVE_PARSER_FIELDS = frozenset(
    {
        "density",
        "evidence_required",
        "files_expected",
        "vision",
        "effort",
        "reasoning_effort",
        "source_ref",
        "tool_op",
    }
)

# Operator-proxy DIRECTIVE inline fields (cdp-operator-proxy §2 / Tier-M
# template) that cursor-auto does not bind onto AutoJob.
_OPERATOR_PROXY_PROSE_FIELDS = frozenset(
    {
        "arc",
        "assumed_state",
        "intent",
        "authority",
        "budget",
        "deadline",
        "ac",
        # Idea-commissioning register (operator-proxy / work-item-seed-path).
        # Commission prose, not AutoJob envelope binds — defer, do not WARN
        # as unknown. ``from_lane`` is not a field (use wire ``lane=``).
        "idea",
        "kind",
        "peer_disclosure",
    }
)


def packet_field_names() -> frozenset[str]:
    """Return DIRECTIVE/operator-proxy prose keys that envelope parity must defer, not warn."""
    return _DIRECTIVE_PARSER_FIELDS | _OPERATOR_PROXY_PROSE_FIELDS


__all__ = ["packet_field_names"]
