"""
Consensus v7 handler registration — contextualized verification.

Registers top-level handlers plus verify and veto sub-pipeline thin handlers.
v7 adds: strip + combine synthesis (replaces post_process + enrich_review).
"""

from __future__ import annotations

from .analyze_question import AnalyzeQuestionHandler
from .answer import ConsensusAnswerHandler
from .combine_passages import CombinePassagesHandler
from .coverage_audit import CoverageAuditHandler
from .filter_negatives import FilterNegativesHandler
from .purge_rejected import PurgeRejectedHandler
from .strip_rejected import StripRejectedHandler
from .synergize import SynergizeHandler
from .verify.handlers import register_handlers as register_verify_handlers
from .veto.handlers import register_handlers as register_veto_handlers


def register_handlers(router) -> None:
    """Register all v7 consensus handlers."""
    router.register_domain_handler_class(
        "consensus", "consensus_answer_v7", ConsensusAnswerHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_analyze_v7", AnalyzeQuestionHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_synergize_v7", SynergizeHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_filter_negatives_v7", FilterNegativesHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_strip_rejected_v7", StripRejectedHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_combine_passages_v7", CombinePassagesHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_purge_rejected_v7", PurgeRejectedHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_coverage_audit_v7", CoverageAuditHandler
    )

    register_verify_handlers(router)
    register_veto_handlers(router)
