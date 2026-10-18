"""Import-time and row-level delegation tier guards for job_vocab."""

from __future__ import annotations

from job_vocab import (
    JOB_RECORDS,
    TIER_A_ORCHESTRATOR_JOBS,
    TIER_B_JUDGMENT_RECON_JOBS,
    TIER_C_LEAF_JOBS,
)


def test_tier_frozensets_partition_job_records() -> None:
    names = frozenset(record.name for record in JOB_RECORDS)
    assert TIER_A_ORCHESTRATOR_JOBS | TIER_B_JUDGMENT_RECON_JOBS | TIER_C_LEAF_JOBS == names
    assert TIER_A_ORCHESTRATOR_JOBS.isdisjoint(TIER_B_JUDGMENT_RECON_JOBS)
    assert TIER_A_ORCHESTRATOR_JOBS.isdisjoint(TIER_C_LEAF_JOBS)
    assert TIER_B_JUDGMENT_RECON_JOBS.isdisjoint(TIER_C_LEAF_JOBS)
