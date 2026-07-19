"""Deviation disposition registry for Lane-A shared-master capture scoring (a:25024)."""

from __future__ import annotations

from enum import StrEnum


class DeviationDisposition(StrEnum):
    HARD_FAIL = "hard_fail"
    ANNOTATE = "annotate"
    CENSUS_ONLY = "census_only"


_DEVIATION_REGISTRY: dict[str, DeviationDisposition] = {
    "divergence:repo_diff_paths_unattributed:ambient:": DeviationDisposition.ANNOTATE,
    "capture:outside_repo_paths_present": DeviationDisposition.ANNOTATE,
    "capture:gitignored_present_unattributed": DeviationDisposition.ANNOTATE,
    "capture:shell_repo_writes_unverified": DeviationDisposition.ANNOTATE,
    "divergence:manifest_vs_git_labels": DeviationDisposition.ANNOTATE,
    "capture:non_file_manifest_entry_dropped": DeviationDisposition.ANNOTATE,
    "capture:cortex_writes_unattributed": DeviationDisposition.ANNOTATE,
    "capture:outside_repo_baseline_missing": DeviationDisposition.ANNOTATE,
    "divergence:repo_diff_paths_unattributed:": DeviationDisposition.HARD_FAIL,
}


def disposition_for_deviation(token: str) -> DeviationDisposition:
    """Return disposition for *token*; untagged tokens fail closed to hard_fail (AC11)."""
    for prefix in sorted(_DEVIATION_REGISTRY, key=len, reverse=True):
        if token.startswith(prefix):
            return _DEVIATION_REGISTRY[prefix]
    return DeviationDisposition.HARD_FAIL


def deviation_degrades_capture_status(token: str) -> bool:
    return disposition_for_deviation(token) == DeviationDisposition.HARD_FAIL
