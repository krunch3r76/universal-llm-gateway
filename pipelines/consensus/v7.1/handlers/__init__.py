"""
Consensus v7.1 handler registration — contextualized verification.

Step name → step_type → handler file (for debugging in pipeline viewer)
────────────────────────────────────────────────────────────────────────

chain-v7.yaml (top-level):
  analyze_question      consensus_analyze_v7_1            analyze_question.py
  answer_all            consensus_answer_v7_1             answer.py
  synergize             consensus_synergize_v7_1          synergize.py
  filter_negatives      consensus_filter_negatives_v7_1   filter_negatives.py
  filter_orphans        consensus_filter_orphans_v7_1     filter_orphans.py
  verify_link0/1/2      sub_pipeline                    → verify/handlers/
  veto_pass             sub_pipeline                    → veto/handlers/
  synthesize            sub_pipeline                    → (below)

synthesize/synthesize.yaml:
  group_facts           consensus_group_facts_v7_1              group_facts.py
  synthesize_answer     consensus_synthesize_batched_v7_1       synthesize_batched.py
  identify_redundancy   consensus_identify_redundancy_v7_1      identify_redundancy.py
  reorganize_answer     consensus_reorganize_v7_1               reorganize_answer.py
  find_uncited_filter   consensus_find_uncited_filter_v7_1      find_uncited_filter.py
  c4_enforce            consensus_assert_then_revise_v7_1       assert_then_revise.py

verify/verify.yaml:
  decompose             consensus_decompose_v7_1          verify/handlers/
  classify_domain       consensus_classify_domain_v7_1    verify/handlers/
  atomicity_gate        consensus_atomicity_gate_v7_1     verify/handlers/
  contextualize         consensus_contextualize_v7_1      verify/handlers/
  domain_verify         consensus_domain_verify_v7_1      verify/handlers/
  verify_general        consensus_verify_general_v7_1     verify/handlers/
  filter_threshold      consensus_filter_threshold_v7_1   verify/handlers/

veto/veto.yaml:
  veto_verify           consensus_veto_verify_v7_1        veto/handlers/
  veto_threshold        consensus_veto_threshold_v7_1     veto/handlers/
"""

from __future__ import annotations

from .analyze_question import AnalyzeQuestionHandler
from .answer import ConsensusAnswerHandler
from .assert_then_revise import AssertThenReviseHandler
from .citation_coverage import (
    citation_coverage_check as _citation_coverage_check,  # noqa: F401
)
from .filter_negatives import FilterNegativesHandler
from .filter_orphans import FilterOrphansHandler
from .find_uncited_filter import FindUncitedFilterHandler
from .group_facts import GroupFactsHandler
from .identify_redundancy import IdentifyRedundancyHandler
from .reorganize_answer import ReorganizeAnswerHandler
from .single_call import SingleCallHandler
from .synergize import SynergizeHandler
from .synthesize_batched import SynthesizeBatchedHandler
from .verify.handlers import register_handlers as register_verify_handlers
from .veto.handlers import register_handlers as register_veto_handlers


def register_handlers(router) -> None:
    """Register all v7 consensus handlers."""
    router.register_domain_handler_class(
        "consensus", "consensus_answer_v7_1", ConsensusAnswerHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_analyze_v7_1", AnalyzeQuestionHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_synergize_v7_1", SynergizeHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_filter_negatives_v7_1", FilterNegativesHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_filter_orphans_v7_1", FilterOrphansHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_single_call_v7_1", SingleCallHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_group_facts_v7_1", GroupFactsHandler
    )
    router.register_domain_handler_class(
        "consensus",
        "consensus_synthesize_batched_v7_1",
        SynthesizeBatchedHandler,
    )
    router.register_domain_handler_class(
        "consensus",
        "consensus_identify_redundancy_v7_1",
        IdentifyRedundancyHandler,
    )
    router.register_domain_handler_class(
        "consensus",
        "consensus_reorganize_v7_1",
        ReorganizeAnswerHandler,
    )
    router.register_domain_handler_class(
        "consensus",
        "consensus_find_uncited_filter_v7_1",
        FindUncitedFilterHandler,
    )
    router.register_domain_handler_class(
        "consensus",
        "consensus_assert_then_revise_v7_1",
        AssertThenReviseHandler,
    )

    register_verify_handlers(router)
    register_veto_handlers(router)
