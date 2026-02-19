"""
Consensus v6.0 handler registration — sub-pipeline architecture.

Registers top-level handlers plus verify and veto sub-pipeline thin handlers.
"""

from __future__ import annotations

from ..verify.handlers import register_verify_handlers
from ..veto.handlers import register_veto_handlers
from .analyze_question import AnalyzeQuestionHandler
from .answer import ConsensusAnswerHandler
from .enrich_reviewer import EnrichReviewerHandler
from .filter_negatives import FilterNegativesHandler
from .post_process import PostProcessHandler
from .synergize import SynergizeHandler


def register_handlers(router) -> None:
    """Register all v6.0 consensus handlers."""
    router.register_domain_handler_class(
        "consensus", "consensus_answer_v3_3", ConsensusAnswerHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_analyze_v5_0", AnalyzeQuestionHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_post_process_v5_0", PostProcessHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_synergize_v5_0", SynergizeHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_filter_negatives_v5_0", FilterNegativesHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_enrich_review_v5_0", EnrichReviewerHandler
    )

    register_verify_handlers(router)
    register_veto_handlers(router)
