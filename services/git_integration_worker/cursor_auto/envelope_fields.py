"""Envelope field vocabulary for contracts with no declared row model (7119 L1).

Derived from ``AutoJob`` rather than hand-enumerated: a field added to the
envelope joins the parity vocabulary without a second edit here (§AC6.4 — a
hand-enumerated field set is a snapshot that rots on the next field addition).
"""

from __future__ import annotations

import dataclasses
from typing import Any

from services.git_integration_worker.cursor_auto.queue import AutoJob

# Runtime-owned AutoJob fields. These are never author-bindable on the wire, so
# an authored key matching one is prose that happens to collide, not a binding.
_RUNTIME_FIELDS = frozenset(
    {
        "job_id",
        "thread_id",
        "turn_number",
        "subject",
        "body",
        "from_agent",
        "to_agent",
        "request_id",
        "continuity_matched_token",
        "wire_dropped_fields",
        "enqueued_at",
        "status",
        "superseded_by",
        "supersedes",
        "superseded_dispatch_id",
        "nested_sdk_finished",
    }
)

# Lane identity — a drop is reportable but does not change what the job ran as.
_DESCRIPTIVE_FIELDS = frozenset({"cse_chat_url", "cse_registration_id"})

# Fail-closed, matching the row-model side: unclassified ⇒ effect.
_DEFAULT_PARITY_CLASS = "effect"


def envelope_field_names() -> frozenset[str]:
    """Author-bindable envelope fields, derived from the ``AutoJob`` declaration."""
    return frozenset(
        f.name for f in dataclasses.fields(AutoJob) if f.name not in _RUNTIME_FIELDS
    )


def parity_class_for_envelope_field(field_name: str) -> str:
    """Return the parity class for *field_name* — ``unknown`` when off-vocabulary."""
    if field_name not in envelope_field_names():
        return "unknown"
    if field_name in _DESCRIPTIVE_FIELDS:
        return "descriptive"
    return _DEFAULT_PARITY_CLASS


def envelope_values_from_job(job: AutoJob) -> dict[str, Any]:
    """The values the job is actually running with, keyed by envelope field."""
    return {name: getattr(job, name, None) for name in envelope_field_names()}


__all__ = [
    "envelope_field_names",
    "envelope_values_from_job",
    "parity_class_for_envelope_field",
]
