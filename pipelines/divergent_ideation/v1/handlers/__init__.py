"""divergent_ideation v1 domain handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .audit_assign import AuditAssignV1Handler
from .collate import CollateV1Handler
from .leak_check import LeakCheckV1Handler
from .operator_split import OperatorSplitV1Handler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    domain = "divergent_ideation"
    pairs = [
        ("leak_check_v1", LeakCheckV1Handler),
        ("operator_split_v1", OperatorSplitV1Handler),
        ("audit_assign_v1", AuditAssignV1Handler),
        ("collate_v1", CollateV1Handler),
    ]
    for step_type, cls in pairs:
        router.register_domain_handler_class(domain, step_type, cls)
