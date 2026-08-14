"""Unearned self-assertion auditor — static enumerable reporter.

Callers: ``scripts/audit-unearned-self-assertion`` and G1/G5 of
``todo:unearned-self-assertion-auditor``. Every coat result is a structured
verdict; ``could_not_check`` is never encoded as an empty clean.
"""

from unearned_self_assertion_auditor.report import (
    CoatResult,
    ReporterReport,
    run_reporter,
)

__all__ = ["CoatResult", "ReporterReport", "run_reporter"]
