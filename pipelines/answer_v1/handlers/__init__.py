"""
Answer_v1 handler registration.

Registers a generate handler that strips a leading <think>...</think> block from
LLM output. Used by the RAG answer pipeline (answer-v1.yaml).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .answer import AnswerGenerateHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Register answer_v1 domain handlers."""
    router.register_domain_handler_class(
        "answer_v1",
        "generate",
        AnswerGenerateHandler,
    )
