"""
Consensus v7 handler registration — contextualized verification.

Registers top-level handlers plus verify and veto sub-pipeline thin handlers.
Synthesize handlers are added incrementally as that sub-pipeline is rebuilt.
"""

from __future__ import annotations

from .analyze_question import AnalyzeQuestionHandler
from .answer import ConsensusAnswerHandler
from .citation_coverage import (
    citation_coverage_check as _citation_coverage_check,  # noqa: F401
)
from .filter_negatives import FilterNegativesHandler
from .single_call import SingleCallHandler
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
        "consensus", "consensus_single_call_v7", SingleCallHandler
    )

    register_verify_handlers(router)
    register_veto_handlers(router)
