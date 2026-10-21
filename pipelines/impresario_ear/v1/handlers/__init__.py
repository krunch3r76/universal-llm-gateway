"""impresario_ear v1 handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .deliver import ImpresarioEarDeliverHandler
from .evaluate import ImpresarioEarEvaluateHandler
from .gather import ImpresarioEarGatherHandler
from .resolve import ImpresarioEarResolveHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "impresario_ear",
        "impresario_ear_resolve_v1",
        ImpresarioEarResolveHandler,
    )
    router.register_domain_handler_class(
        "impresario_ear",
        "impresario_ear_gather_v1",
        ImpresarioEarGatherHandler,
    )
    router.register_domain_handler_class(
        "impresario_ear",
        "impresario_ear_evaluate_v1",
        ImpresarioEarEvaluateHandler,
    )
    router.register_domain_handler_class(
        "impresario_ear",
        "impresario_ear_deliver_v1",
        ImpresarioEarDeliverHandler,
    )


__all__ = [
    "ImpresarioEarDeliverHandler",
    "ImpresarioEarEvaluateHandler",
    "ImpresarioEarGatherHandler",
    "ImpresarioEarResolveHandler",
    "register_handlers",
]
