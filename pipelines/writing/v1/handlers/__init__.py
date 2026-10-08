"""Register writer-specialist v1 handlers on the writing domain.

The pipeline loader calls ``register_handlers`` while scanning
``pipelines/writing``. The four step types cover assemble, provenance,
independence, and the final UNSENT envelope. Generate steps stay on the
built-in generate handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .assemble import WritingAssembleHandler
from .finalize import WritingFinalizeHandler
from .independence import WritingIndependenceHandler
from .provenance_check import WritingProvenanceCheckHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Bind the four writing v1 step types on ``router``.

    Domain is ``writing``. Each class's ``step_type`` matches the key
    passed here. Later pipeline versions must use a different suffix so
    this registration is not overwritten.
    """
    router.register_domain_handler_class(
        "writing",
        "writing_assemble_v1",
        WritingAssembleHandler,
    )
    router.register_domain_handler_class(
        "writing",
        "writing_provenance_check_v1",
        WritingProvenanceCheckHandler,
    )
    router.register_domain_handler_class(
        "writing",
        "writing_independence_v1",
        WritingIndependenceHandler,
    )
    router.register_domain_handler_class(
        "writing",
        "writing_finalize_v1",
        WritingFinalizeHandler,
    )
