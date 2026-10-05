"""closeout-memo v1 handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .admit import CloseoutMemoAdmitHandler
from .coalesce import CloseoutMemoCoalesceHandler
from .deliver import CloseoutMemoDeliverHandler
from .fallback import CloseoutMemoFallbackHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "closeout_memo",
        "closeout_memo_admit_v1",
        CloseoutMemoAdmitHandler,
    )
    router.register_domain_handler_class(
        "closeout_memo",
        "closeout_memo_coalesce_v1",
        CloseoutMemoCoalesceHandler,
    )
    router.register_domain_handler_class(
        "closeout_memo",
        "closeout_memo_deliver_v1",
        CloseoutMemoDeliverHandler,
    )
    router.register_domain_handler_class(
        "closeout_memo",
        "closeout_memo_fallback_v1",
        CloseoutMemoFallbackHandler,
    )
