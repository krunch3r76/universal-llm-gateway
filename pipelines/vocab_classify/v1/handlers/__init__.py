"""vocab_classify v1 domain handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .bundle import VocabClassifyBundleV1Handler
from .reconcile import VocabClassifyReconcileV1Handler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Register vocab_classify v1 step types."""
    domain = "vocab_classify"
    router.register_domain_handler_class(
        domain,
        "vocab_classify_bundle_v1",
        VocabClassifyBundleV1Handler,
    )
    router.register_domain_handler_class(
        domain,
        "vocab_classify_reconcile_v1",
        VocabClassifyReconcileV1Handler,
    )
