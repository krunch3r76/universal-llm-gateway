"""
Gateway selection package - Decision Engine based.

Usage:
    from systems.routing.selection.decision import (
        DecisionEngine,
        load_routing_policy,
    )
    from systems.routing.selection import (
        Gateway,
        Placement,
        collect_gateways,
        build_placement,
    )

    policy = load_routing_policy(config)
    engine = DecisionEngine(policy)
    selected, trace = engine.select(gateways, placement)
"""

from .catalog import (
    collect_stargate_model_sets,
    get_activated_models_for_display,
    get_all_available_models,
    get_model_source_map,
    is_model_in_any_catalog,
)
from .collector import build_placement, collect_gateways

# Legacy selector functions removed - use DecisionEngine instead
from .types import Gateway, Placement, Predicate, Scorer, SelectionResult

__all__ = [
    # Types
    "Gateway",
    "Placement",
    "SelectionResult",
    "Predicate",
    "Scorer",
    # Main API - use DecisionEngine from decision package
    # Catalog functions
    "collect_stargate_model_sets",
    "get_all_available_models",
    "is_model_in_any_catalog",
    "get_activated_models_for_display",
    "get_model_source_map",
    # Collectors
    "collect_gateways",
    "build_placement",
]
