"""Thin handlers for the veto sub-pipeline (v7)."""

from __future__ import annotations

from .veto_threshold import VetoThresholdHandler
from .veto_verify import VetoVerifyHandler


def register_handlers(router) -> None:
    """Register veto sub-pipeline handlers."""
    router.register_domain_handler_class(
        "consensus", "consensus_veto_verify_v7", VetoVerifyHandler
    )
    router.register_domain_handler_class(
        "consensus", "consensus_veto_threshold_v7", VetoThresholdHandler
    )
