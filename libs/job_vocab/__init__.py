"""Job vocabulary. Importers take derived sets from this module."""

from __future__ import annotations

from job_vocab.records import (
    CURSOR_AUTO_ADMITTED_JOBS,
    GENERATE_ADMITTED_JOBS,
    HANDOFF_ADMITTED_JOBS,
    HARNESS_STACK_SKIP_JOBS,
    HYPOTHESIZE_ON_JOBS,
    INLINE_ONLY_JOBS,
    JOB_RECORDS,
    POSTURE_SKIP_JOBS,
    SOURCE_REF_JOBS,
    TO_THREAD_ADMITTED_JOBS,
    JobRecord,
    job_record,
    registry_ref_for,
)

__all__ = [
    "CURSOR_AUTO_ADMITTED_JOBS",
    "GENERATE_ADMITTED_JOBS",
    "HANDOFF_ADMITTED_JOBS",
    "HARNESS_STACK_SKIP_JOBS",
    "HYPOTHESIZE_ON_JOBS",
    "INLINE_ONLY_JOBS",
    "JOB_RECORDS",
    "POSTURE_SKIP_JOBS",
    "SOURCE_REF_JOBS",
    "TO_THREAD_ADMITTED_JOBS",
    "JobRecord",
    "job_record",
    "registry_ref_for",
]
