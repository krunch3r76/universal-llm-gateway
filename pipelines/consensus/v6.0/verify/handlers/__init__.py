"""
Thin handlers for the verify sub-pipeline (v6.0).

Each handler wraps a single function from shared/_chain_* modules,
making the verification flow declarative via verify.yaml.
"""

from __future__ import annotations

from .atomicity_gate import AtomicityGateHandler
from .classify_domain import ClassifyDomainHandler
from .decompose import DecomposeHandler
from .decompose_compound import DecomposeCompoundHandler
from .domain_verify import DomainVerifyHandler
from .filter_threshold import FilterThresholdHandler
from .verify_general import VerifyGeneralHandler


def register_verify_handlers(router) -> None:
    """Register all v6.0 verify sub-pipeline handlers."""
    router.register_domain_handler_class(
        "consensus", "consensus_decompose_v6_0", DecomposeHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_classify_domain_v6_0", ClassifyDomainHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_decompose_compound_v6_0", DecomposeCompoundHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_atomicity_gate_v6_0", AtomicityGateHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_domain_verify_v6_0", DomainVerifyHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_verify_general_v6_0", VerifyGeneralHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_filter_threshold_v6_0", FilterThresholdHandler
    )
