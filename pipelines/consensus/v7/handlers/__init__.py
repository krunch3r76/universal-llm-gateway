"""
Consensus v7 handler registration — contextualized verification.

Step name → step_type → handler file (for debugging in pipeline viewer)
────────────────────────────────────────────────────────────────────────

chain-v7.yaml (top-level):
  analyze_question      consensus_analyze_v7            analyze_question.py
  answer_all            consensus_answer_v7             answer.py
  synergize             consensus_synergize_v7          synergize.py
  filter_negatives      consensus_filter_negatives_v7   filter_negatives.py
  verify_link0/1/2      sub_pipeline                    → verify/handlers/
  veto_pass             sub_pipeline                    → veto/handlers/
  synthesize            sub_pipeline                    → (below)

synthesize/synthesize.yaml:
  group_facts           consensus_group_facts_v7        group_facts.py
  synthesize_answer     assess_loop_v1                  tools/handlers/assess_loop.py
                          assess_handler:               citation_coverage.py
                            citation_coverage_check

verify/verify.yaml:
  decompose             consensus_decompose_v7          verify/handlers/
  classify_domain       consensus_classify_domain_v7    verify/handlers/
  atomicity_gate        consensus_atomicity_gate_v7     verify/handlers/
  contextualize         consensus_contextualize_v7      verify/handlers/
  domain_verify         consensus_domain_verify_v7      verify/handlers/
  verify_general        consensus_verify_general_v7     verify/handlers/
  filter_threshold      consensus_filter_threshold_v7   verify/handlers/

veto/veto.yaml:
  veto_verify           consensus_veto_verify_v7        veto/handlers/
  veto_threshold        consensus_veto_threshold_v7     veto/handlers/
"""

from __future__ import annotations

from .analyze_question import AnalyzeQuestionHandler
from .answer import ConsensusAnswerHandler
from .citation_coverage import (
    citation_coverage_check as _citation_coverage_check,  # noqa: F401
)
from .filter_negatives import FilterNegativesHandler
from .group_facts import GroupFactsHandler
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
    router.register_domain_handler_class(
        "consensus", "consensus_group_facts_v7", GroupFactsHandler
    )

    register_verify_handlers(router)
    register_veto_handlers(router)
