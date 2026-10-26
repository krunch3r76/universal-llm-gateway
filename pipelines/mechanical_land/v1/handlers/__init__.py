"""mechanical_land v1 handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .commit import MechanicalLandCommitHandler
from .plan import MechanicalLandPlanHandler
from .render_packet import MechanicalLandRenderPacketHandler
from .stamp_policy import MechanicalLandStampPolicyHandler
from .transition import MechanicalLandTransitionHandler
from .validate import MechanicalLandValidateHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "mechanical_land",
        "mechanical_land_validate_v1",
        MechanicalLandValidateHandler,
    )
    router.register_domain_handler_class(
        "mechanical_land",
        "mechanical_land_stamp_policy_v1",
        MechanicalLandStampPolicyHandler,
    )
    router.register_domain_handler_class(
        "mechanical_land",
        "mechanical_land_transition_v1",
        MechanicalLandTransitionHandler,
    )
    router.register_domain_handler_class(
        "mechanical_land",
        "mechanical_land_render_packet_v1",
        MechanicalLandRenderPacketHandler,
    )
    router.register_domain_handler_class(
        "mechanical_land",
        "mechanical_land_plan_v1",
        MechanicalLandPlanHandler,
    )
    router.register_domain_handler_class(
        "mechanical_land",
        "mechanical_land_commit_v1",
        MechanicalLandCommitHandler,
    )


__all__ = ["register_handlers"]
