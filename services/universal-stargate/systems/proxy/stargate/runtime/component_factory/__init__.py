"""Stargate component factory package – public initialization surface.

This package is the modularized successor to the original monolithic
``component_factory.py`` (585+ SLOC). It was split into eight focused modules
(7 domain modules + this ``__init__.py``) while preserving the exact public
import surface.

All 11 public symbols that were previously defined at the top level of
``runtime.component_factory`` are re-exported here. Existing call sites in
``startup.py``, ``gateway_bootstrap.py``, and tests continue to work without
any import changes:

    from ...runtime import component_factory
    component_factory.initialize_request_components(proxy)
    # or
    from ...runtime.component_factory import initialize_pipeline_system

Private helpers (names starting with ``_``) are intentionally *not* re-exported.

Modules:
- manager_wiring                 – Token/ParameterManager HTTP wiring
- profile_transformation_bootstrap – config dir, TransformationEngine, ProfileManager
- request_component_bootstrap    – local vs. Master request pipeline construction
- profile_hot_reload             – HotReloadWatcher for profiles
- pipeline_registry_bootstrap    – PipelineRegistry/Executor + reload subscriptions
- aggregate_availability_bootstrap – AggregateModelAvailabilityEmitter wiring
- intelligence_profile_bootstrap – IntelligenceProfileStore + cloud derivation
"""

from __future__ import annotations

# Re-export the 11 public symbols that formed the original API surface.
# Order matches the historical definition order in component_factory.py for
# ease of diff review.
from .aggregate_availability_bootstrap import (
    initialize_aggregate_model_availability,
)
from .intelligence_profile_bootstrap import initialize_intelligence_profiles
from .manager_wiring import configure_token_and_parameter_managers
from .pipeline_registry_bootstrap import (
    create_model_checker,
    initialize_pipeline_system,
)
from .profile_hot_reload import initialize_hot_reload
from .profile_transformation_bootstrap import (
    initialize_profile_manager,
    initialize_transformation_engine,
)
from .request_component_bootstrap import (
    create_token_allocation_policy,
    initialize_master_request_components,
    initialize_request_components,
)

__all__ = [
    "configure_token_and_parameter_managers",
    "initialize_transformation_engine",
    "initialize_profile_manager",
    "create_token_allocation_policy",
    "initialize_request_components",
    "initialize_master_request_components",
    "initialize_hot_reload",
    "create_model_checker",
    "initialize_aggregate_model_availability",
    "initialize_intelligence_profiles",
    "initialize_pipeline_system",
]
