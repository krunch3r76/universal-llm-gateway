"""
Code-review pipeline handler registration.

Step name → step_type → handler file
────────────────────────────────────────────────
  merge       code_review_merge_findings    merge_findings.py
"""

from __future__ import annotations

from .merge_findings import MergeFindingsHandler


def register_handlers(router) -> None:
    """Register code-review domain handlers."""
    router.register_domain_handler_class(
        "code_review", "code_review_merge_findings", MergeFindingsHandler
    )
