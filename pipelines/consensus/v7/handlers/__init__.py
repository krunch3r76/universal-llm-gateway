"""
Consensus v7 handler registration — contextualized verification.

Registers top-level handlers plus verify and veto sub-pipeline thin handlers.
v7 synthesis is outline-first (organize → review → synthesize → coverage).
"""

from __future__ import annotations

from .analyze_question import AnalyzeQuestionHandler
from .answer import ConsensusAnswerHandler
from .coverage_audit import CoverageAuditHandler
from .coverage_review import CoverageReviewHandler
from .filter_negatives import FilterNegativesHandler
from .organize_facts import OrganizeFactsHandler
from .outline_review import OutlineReviewHandler
from .section_synthesize import SectionSynthesizeHandler
from .single_call import SingleCallHandler
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
        "consensus", "consensus_coverage_audit_v7", CoverageAuditHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_coverage_review_v7", CoverageReviewHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_organize_facts_v7", OrganizeFactsHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_outline_review_v7", OutlineReviewHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_section_synthesize_v7", SectionSynthesizeHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_single_call_v7", SingleCallHandler
    )

    register_verify_handlers(router)
    register_veto_handlers(router)
