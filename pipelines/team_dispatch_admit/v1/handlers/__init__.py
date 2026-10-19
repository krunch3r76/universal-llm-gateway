"""team_dispatch_admit v1 handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .assemble import TeamDispatchAdmitAssembleHandler
from .defaults import TeamDispatchAdmitDefaultsHandler
from .validate import TeamDispatchAdmitValidateHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "team_dispatch_admit",
        "team_dispatch_admit_validate_v1",
        TeamDispatchAdmitValidateHandler,
    )
    router.register_domain_handler_class(
        "team_dispatch_admit",
        "team_dispatch_admit_defaults_v1",
        TeamDispatchAdmitDefaultsHandler,
    )
    router.register_domain_handler_class(
        "team_dispatch_admit",
        "team_dispatch_admit_assemble_v1",
        TeamDispatchAdmitAssembleHandler,
    )
