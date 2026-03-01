"""
Thin handlers for the verify sub-pipeline (v7).

Each handler wraps a single function from shared/_chain_* modules,
making the verification flow declarative via verify.yaml.

v7 adds: ContextualizeHandler — rewrites claims as self-standing
statements with topic anchoring before cross-model verification.
"""

from __future__ import annotations

from .atomicity_gate import AtomicityGateHandler
from .classify_domain import ClassifyDomainHandler
from .contextualize import ContextualizeHandler
from .decompose import DecomposeHandler
from .domain_verify import DomainVerifyHandler
from .filter_threshold import FilterThresholdHandler
from .verify_general import VerifyGeneralHandler


def register_handlers(router) -> None:
    """Register all v7 verify sub-pipeline handlers."""
    router.register_domain_handler_class(
        "consensus", "consensus_decompose_v7_1", DecomposeHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_classify_domain_v7_1", ClassifyDomainHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_atomicity_gate_v7_1", AtomicityGateHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_contextualize_v7_1", ContextualizeHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_domain_verify_v7_1", DomainVerifyHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_verify_general_v7_1", VerifyGeneralHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_filter_threshold_v7_1", FilterThresholdHandler
    )
