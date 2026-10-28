"""Register writer-specialist v1 handlers on the writing domain.

The pipeline loader calls ``register_handlers`` while scanning
``pipelines/writing``. Registered step types are assemble, provenance_check, independence, finalize,
seat_dispatch, seat_wait, seat_select, and review_local. Draft and revise
generate steps stay on the built-in generate handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .assemble import WritingAssembleHandler
from .draft_selector import WritingDraftSelectorHandler
from .finalize import WritingFinalizeHandler
from .independence import WritingIndependenceHandler
from .obligation_coverage import WritingObligationCoverageHandler
from .prompt_architect import WritingPromptArchitectHandler
from .prose_rhythm import WritingProseRhythmHandler
from .provenance_check import WritingProvenanceCheckHandler
from .review_local import WritingReviewLocalHandler
from .seat_dispatch import WritingReviewOpenaiHandler, WritingSeatDispatchHandler
from .seat_wait import (
    WritingReviewOpenaiWaitHandler,
    WritingSeatSelectHandler,
    WritingSeatWaitHandler,
)
from .semantic_judge import WritingSemanticJudgeHandler
from .source_obligations import WritingSourceObligationsHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    """Bind the writing v1 step types on ``router``.

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
        "writing_source_obligations_v1",
        WritingSourceObligationsHandler,
    )
    router.register_domain_handler_class(
        "writing",
        "writing_prompt_architect_v1",
        WritingPromptArchitectHandler,
    )
    router.register_domain_handler_class(
        "writing",
        "writing_obligation_coverage_v1",
        WritingObligationCoverageHandler,
    )
    router.register_domain_handler_class(
        "writing",
        "writing_draft_selector_v1",
        WritingDraftSelectorHandler,
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
    router.register_domain_handler_class(
        "writing",
        "writing_seat_dispatch_v1",
        WritingSeatDispatchHandler,
    )
    router.register_domain_handler_class(
        "writing",
        "writing_seat_wait_v1",
        WritingSeatWaitHandler,
    )
    router.register_domain_handler_class(
        "writing",
        "writing_seat_select_v1",
        WritingSeatSelectHandler,
    )
    router.register_domain_handler_class(
        "writing",
        "writing_review_local_v1",
        WritingReviewLocalHandler,
    )
    router.register_domain_handler_class(
        "writing",
        "writing_review_openai_v1",
        WritingReviewOpenaiHandler,
    )
    router.register_domain_handler_class(
        "writing",
        "writing_review_openai_wait_v1",
        WritingReviewOpenaiWaitHandler,
    )
    router.register_domain_handler_class(
        "writing",
        "writing_semantic_judge_v1",
        WritingSemanticJudgeHandler,
    )
    router.register_domain_handler_class(
        "writing",
        "writing_prose_rhythm_v1",
        WritingProseRhythmHandler,
    )
