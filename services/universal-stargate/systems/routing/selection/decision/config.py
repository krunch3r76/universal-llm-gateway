"""
Affinity and routing policy configuration.

Config loaded from stargate_config.yaml and validated at startup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from model_id import ModelId


class AffinityStrength(StrEnum):
    """Affinity rule strength - determines selection behavior."""

    SOFT = "soft"  # Bonus points, can be overridden by other factors
    HARD = "hard"  # Must route to this gateway if feasible


@dataclass(frozen=True, kw_only=True)
class AffinityRule:
    """
    Single affinity rule mapping model pattern to stargate.

    Invariant: ∀ rule, (match is valid regex OR exact string)
               ∧ stargate ∈ configured_stargates
    """

    match: str  # Exact model ID or regex pattern
    stargate: str  # Target stargate ID
    strength: AffinityStrength = AffinityStrength.SOFT
    bonus: float = 50.0  # Score bonus for soft affinity
    evict_if_needed: bool = True  # Allow eviction to satisfy this rule

    _compiled_pattern: re.Pattern[str] | None = field(
        default=None, compare=False, repr=False
    )

    def __post_init__(self) -> None:
        """Compile regex pattern if match contains wildcards."""
        if "*" in self.match or "?" in self.match or self.match.startswith("^"):
            # Treat as regex/glob
            pattern = self.match.replace("*", ".*").replace("?", ".")
            if not pattern.startswith("^"):
                pattern = f"^{pattern}$"
            compiled = re.compile(pattern, re.IGNORECASE)
            object.__setattr__(self, "_compiled_pattern", compiled)

    def matches(self, model_id: ModelId | str) -> bool:
        """
        Check if this rule matches the given model ID using ModelId normalization.

        Supports both ModelId objects and strings. For exact matches, uses
        normalized comparison (handles -hybrid suffix automatically).

        Args:
            model_id: ModelId object or string to match against

        Returns:
            True if rule matches model_id (normalized comparison)

        Examples:
            rule = AffinityRule(match="hermes3-llama-3.1-70b-uncensored-16384", ...)
            rule.matches("hermes3-llama-3.1-70b-uncensored-16384-hybrid")  # True
            rule.matches(
                ModelId.parse("hermes3-llama-3.1-70b-uncensored-16384-hybrid")
            )  # True
        """
        from model_id import ModelId

        # Parse incoming model_id if it's a string
        if isinstance(model_id, str):
            try:
                incoming = ModelId.parse(model_id)
            except ValueError:
                # Fallback to string match if parsing fails
                return self.match == model_id
        else:
            incoming = model_id

        # Handle pattern matches (wildcards like "hermes3-*")
        if self._compiled_pattern:
            # For patterns, match against original string representation
            model_str = str(incoming)
            match_result = bool(self._compiled_pattern.match(model_str))
            return match_result

        # For exact matches, parse rule pattern and use ModelId.__eq__
        try:
            rule_parsed = ModelId.parse(self.match)
            return (
                incoming == rule_parsed
            )  # Uses ModelId.__eq__ (normalized comparison)
        except ValueError:
            # Fallback to string match if rule pattern doesn't parse as ModelId
            return self.match == str(incoming)


@dataclass(frozen=True, kw_only=True)
class ScoringWeights:
    """Weights for utility scoring components."""

    affinity: float = 50.0  # Affinity rule match
    warm: float = 15.0  # Model already loaded (reduced to allow load balancing)
    slack: float = 10.0  # VRAM headroom after load
    contention: float = (
        20.0  # Low contention reward (positive = fewer requests is better)
    )
    staleness: float = 0.0  # Disabled: causes feedback loop
    stability: float = 5.0  # Hysteresis bonus

    # Eviction-specific
    eviction_base: float = -30.0  # Base penalty for any eviction
    eviction_per_model: float = -20.0  # Per-model eviction penalty

    # Cold-load spreading weights
    empty_gateway: float = 300.0  # High weight to prefer empty gateways for cold loads
    busy_models: float = 20.0  # Moderate weight for busy-count differentiation


@dataclass(frozen=True, kw_only=True)
class RoutingPolicy:
    """
    Complete routing policy configuration.

    Invariant: eviction_margin >= 0 ∧ telemetry_max_age_ms > 0
    Invariant: sticky ⟹ ∃! gateway where model loaded
    """

    eviction_margin: float = (
        10.0  # Score margin required to prefer eviction (reduced for load balancing)
    )
    telemetry_max_age_ms: int = 2000  # Stale telemetry threshold

    weights: ScoringWeights = field(default_factory=ScoringWeights)
    affinity_rules: tuple[AffinityRule, ...] = ()

    # Event emission config
    emit_decision_traces: bool = True  # Enable decision trace emission
    include_candidate_details: bool = False  # Include full candidate info (expensive)

    # Sticky routing configuration
    default_sticky: bool = True
    sticky_overrides: dict[str, bool] = field(default_factory=dict)

    # Resource margin configuration (for _check_resources)
    # Stored as dict to pass through to feasibility checks
    resource_margins: dict[str, float] = field(default_factory=dict)

    def find_affinity(self, model_id: ModelId | str) -> AffinityRule | None:
        """
        Find first matching affinity rule for model.

        Args:
            model_id: ModelId object or string to match

        Returns:
            First matching AffinityRule, or None
        """
        for rule in self.affinity_rules:
            match_result = rule.matches(model_id)  # Now accepts ModelId | str
            if match_result:
                return rule
        return None

    def find_hard_affinity(self, model_id: ModelId | str) -> AffinityRule | None:
        """Find hard affinity rule for model, if any."""
        rule = self.find_affinity(model_id)
        if rule and rule.strength == AffinityStrength.HARD:
            return rule
        return None

    def is_sticky(self, model_id: ModelId | str) -> bool:
        """
        Determine if model should use sticky routing.

        Args:
            model_id: ModelId object or string

        Returns:
            True if model should route to single gateway only
        """
        from model_id import ModelId

        # Convert to string for lookup (sticky_overrides uses strings)
        if isinstance(model_id, ModelId):
            model_id_str = str(model_id)
            normalized_str = model_id.normalized
        else:
            model_id_str = model_id
            try:
                parsed = ModelId.parse(model_id)
                normalized_str = parsed.normalized
            except ValueError:
                normalized_str = model_id_str

        # Check exact match first, then normalized
        if model_id_str in self.sticky_overrides:
            return self.sticky_overrides[model_id_str]
        if normalized_str in self.sticky_overrides:
            return self.sticky_overrides[normalized_str]
        return self.default_sticky


def load_routing_policy(config: dict[str, Any]) -> RoutingPolicy:
    """
    Load routing policy from config dict.

    Expected config shape:
    ```yaml
    routing:
      scoring:
        eviction_margin: 10
        telemetry_max_age_ms: 2000
        capacity:
          enabled: true
        weights:
          affinity: 50
          warm: 15
          contention: 20
          ...
      affinity:
        - match: "hermes3-*"
          stargate: "jupiter"
          strength: "hard"
          evict_if_needed: true
    model_routing:
      default_sticky: true
      sticky_overrides:
        "model-id": false
    ```

    Raises:
        ValueError: If config is invalid
    """
    routing = config.get("routing", {})
    scoring = routing.get("scoring", {})

    # Parse weights
    weight_dict = scoring.get("weights", {})
    eviction_dict = scoring.get("eviction_penalty", {})

    weights = ScoringWeights(
        affinity=weight_dict.get("affinity", 50.0),
        warm=weight_dict.get("warm", 15.0),
        slack=weight_dict.get("slack", 10.0),
        contention=weight_dict.get("contention", 20.0),
        staleness=weight_dict.get("staleness", 0.0),  # Disabled: causes feedback loop
        stability=weight_dict.get("stability", 5.0),
        eviction_base=eviction_dict.get("base", -30.0),
        eviction_per_model=eviction_dict.get("per_model", -20.0),
        empty_gateway=weight_dict.get("empty_gateway", 300.0),
        busy_models=weight_dict.get("busy_models", 20.0),
    )

    # Capacity is managed by Gateway's FifoCapacityGate (parallel_slots)

    # Parse affinity rules
    affinity_list = routing.get("affinity", [])
    rules = []
    for rule_dict in affinity_list:
        if not isinstance(rule_dict, dict):
            continue

        # Legacy format check - fail-fast
        if "gateway" in rule_dict and "stargate" not in rule_dict:
            gw_value = rule_dict["gateway"]
            raise ValueError(
                f"MIGRATION REQUIRED: Affinity rule uses deprecated 'gateway' field.\n"
                f"Rename 'gateway: {gw_value}' to 'stargate: {gw_value}'\n"
                f"See: docs/refactoring/federation-only-architecture-v2.md"
            )

        if "match" not in rule_dict or "stargate" not in rule_dict:
            continue

        strength_str = rule_dict.get("strength", "soft").lower()
        strength = (
            AffinityStrength.HARD if strength_str == "hard" else AffinityStrength.SOFT
        )

        rules.append(
            AffinityRule(
                match=rule_dict["match"],
                stargate=rule_dict["stargate"],
                strength=strength,
                bonus=float(rule_dict.get("bonus", 50.0)),
                evict_if_needed=rule_dict.get("evict_if_needed", True),
            )
        )

    # Parse event emission config
    events_config = routing.get("events", {})
    emit_decision_traces = events_config.get("emit_decision_traces", True)
    include_candidate_details = events_config.get("include_candidate_details", False)

    # Parse sticky routing config from model_routing section
    model_routing = config.get("model_routing", {})
    default_sticky = model_routing.get("default_sticky", True)
    sticky_overrides = model_routing.get("sticky_overrides", {})

    # Parse resource margins (optional, for VRAM safety margins)
    resource_margins = scoring.get("resource_margins", {})

    return RoutingPolicy(
        eviction_margin=scoring.get("eviction_margin", 10.0),
        telemetry_max_age_ms=scoring.get("telemetry_max_age_ms", 2000),
        weights=weights,
        affinity_rules=tuple(rules),
        emit_decision_traces=emit_decision_traces,
        include_candidate_details=include_candidate_details,
        default_sticky=default_sticky,
        sticky_overrides=sticky_overrides,
        resource_margins=resource_margins,
    )
