"""Deviation disposition registry for Lane-A shared-master capture scoring (a:25024)."""

from __future__ import annotations

from enum import StrEnum


class DeviationDisposition(StrEnum):
    HARD_FAIL = "hard_fail"
    ANNOTATE = "annotate"
    CENSUS_ONLY = "census_only"


class DegradeTarget(StrEnum):
    WORK = "work"
    CAPTURE = "capture"
    CENSUS = "census"


_DEVIATION_REGISTRY: dict[str, tuple[DeviationDisposition, DegradeTarget]] = {
    "divergence:repo_diff_paths_unattributed:ambient:": (
        DeviationDisposition.CENSUS_ONLY,
        DegradeTarget.CENSUS,
    ),
    "capture:outside_repo_paths_present": (
        DeviationDisposition.ANNOTATE,
        DegradeTarget.CAPTURE,
    ),
    "capture:gitignored_present_unattributed": (
        DeviationDisposition.ANNOTATE,
        DegradeTarget.CAPTURE,
    ),
    "capture:shell_repo_writes_unverified": (
        DeviationDisposition.ANNOTATE,
        DegradeTarget.CAPTURE,
    ),
    "divergence:manifest_vs_git_labels": (
        DeviationDisposition.ANNOTATE,
        DegradeTarget.CAPTURE,
    ),
    "capture:non_file_manifest_entry_dropped": (
        DeviationDisposition.ANNOTATE,
        DegradeTarget.CAPTURE,
    ),
    "capture:cortex_writes_unattributed": (
        DeviationDisposition.ANNOTATE,
        DegradeTarget.CAPTURE,
    ),
    "capture:outside_repo_baseline_missing": (
        DeviationDisposition.ANNOTATE,
        DegradeTarget.CAPTURE,
    ),
    "capture:polarity_unproved:": (
        DeviationDisposition.ANNOTATE,
        DegradeTarget.CAPTURE,
    ),
    "capture:expected_paths_all_malformed:": (
        DeviationDisposition.ANNOTATE,
        DegradeTarget.CAPTURE,
    ),
    "divergence:repo_diff_paths_unattributed:": (
        DeviationDisposition.HARD_FAIL,
        DegradeTarget.WORK,
    ),
    "capture:stated_intent_no_write_violation:": (
        DeviationDisposition.HARD_FAIL,
        DegradeTarget.WORK,
    ),
    "divergence:lane_b_workspaces_write:": (
        DeviationDisposition.HARD_FAIL,
        DegradeTarget.WORK,
    ),
}


def disposition_for_deviation(token: str) -> DeviationDisposition:
    """Return disposition for *token*; untagged tokens fail closed to hard_fail (AC11)."""
    entry = _registry_entry(token)
    return entry[0] if entry else DeviationDisposition.HARD_FAIL


def degrade_target_for_deviation(token: str) -> DegradeTarget:
    """Return which field a deviation may degrade; untagged → capture."""
    entry = _registry_entry(token)
    return entry[1] if entry else DegradeTarget.CAPTURE


def _registry_entry(token: str) -> tuple[DeviationDisposition, DegradeTarget] | None:
    for prefix in sorted(_DEVIATION_REGISTRY, key=len, reverse=True):
        if token.startswith(prefix):
            return _DEVIATION_REGISTRY[prefix]
    return None


def deviation_degrades_capture_status(token: str) -> bool:
    return disposition_for_deviation(token) == DeviationDisposition.HARD_FAIL


def deviation_caps_work_at_unverified(token: str) -> bool:
    """I3 — contract breaches cap work_outcome at unverified, never not_shipped."""
    return degrade_target_for_deviation(token) == DegradeTarget.WORK


def any_hard_fail_deviation(*tokens: str | None) -> bool:
    """True when any token is a HARD_FAIL disposition (a:25136 narrowing)."""
    return any(token and deviation_degrades_capture_status(token) for token in tokens)
