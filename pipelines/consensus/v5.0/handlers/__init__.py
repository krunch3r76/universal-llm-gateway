"""
Consensus v5.0 handler registration — parallel verify + synergize.

Self-contained: registers all step types used by v5.0 chains.
Step types are version-namespaced (*_v5_0) to avoid collisions with
v4.0 originals and other versions loaded in the same process.
"""

from __future__ import annotations

from .analyze_question import AnalyzeQuestionHandler
from .answer import ConsensusAnswerHandler
from .enrich_reviewer import EnrichReviewerHandler
from .filter_negatives import FilterNegativesHandler
from .post_process import PostProcessHandler
from .synergize import SynergizeHandler
from .verify_chain import VerifyChainHandler
from .veto_pass import VetoPassHandler


def register_handlers(router) -> None:
    """Register all v5.0 consensus handlers under v5_0-namespaced step types."""
    router.register_domain_handler_class(
        "consensus", "consensus_answer_v3_3", ConsensusAnswerHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_analyze_v5_0", AnalyzeQuestionHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_verify_chain_v5_0", VerifyChainHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_veto_pass_v5_0", VetoPassHandler
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
