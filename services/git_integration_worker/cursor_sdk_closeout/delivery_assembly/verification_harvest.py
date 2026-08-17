"""Gate-D verification, both lint gates, observed-pytest harvest, and prose-claim discrepancy.

Runs only the verification slice of assembly. Lint callables are reached via
``lint_verification.<name>`` so ``run_touched_files_lint`` monkeypatches still
apply. Keep the monolith's ``baseline_deviations_list = list(baseline_deviations)``
copy before ``append_harvest_demotion_deviations`` — that copy already exists
and is the list mutated in place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from implement_admission.closeout_models import Verification

from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
from services.git_integration_worker.cursor_sdk_test_observation import (
    annotate_test_observation_discrepancy,
    append_harvest_demotion_deviations,
    extract_prose_test_claim,
    harvest_test_verifications,
)

from .. import deliverable_probe
from .. import lint_verification as lint_verification_mod
from ..closeout_records import SdkRunOutcome


def harvest_closeout_verification(
    *,
    baseline: dict[str, Any] | None,
    verification_cs: ChangeSet,
    outcome: SdkRunOutcome,
    sidecar_path: Path,
    files_expected: list[str],
    write_tree: Path,
    repo_change_set: ChangeSet,
    baseline_deviations: list[str],
    text: str,
) -> tuple[list[Verification], list[str]]:
    """Return (verification, baseline_deviations) after lint + pytest harvest.

    The returned baseline_deviations list is the post-copy object the rest of
    assembly must use (do not keep using the pre-copy list).
    """
    if baseline is None:
        verification = []
    else:
        verification = deliverable_probe.verify_deliverables(
            spec=None,
            change_set=verification_cs,
            outcome=outcome,
            sidecar_path=sidecar_path,
            files_expected=files_expected,
            baseline=baseline,
            source_repo=write_tree,
        )
        lint_row, lint_deviation = lint_verification_mod.run_touched_files_lint(
            write_tree, repo_change_set
        )
        verification = [*verification, lint_row]
        if lint_deviation:
            baseline_deviations.append(lint_deviation)
        giw_f821_verification, giw_f821_deviation = lint_verification_mod.run_giw_subtree_f821_lint(
            write_tree
        )
        verification = [*verification, giw_f821_verification]
        if giw_f821_deviation:
            baseline_deviations.append(giw_f821_deviation)
    # Harvest observed pytest siblings from stream tool_calls regardless of
    # baseline / contract (G1: non-implement harvest still owed). Absence does
    # not earn "no tests ran" (presence_legible_absence_not).
    verification = [
        *verification,
        *harvest_test_verifications(outcome.tool_calls),
    ]
    baseline_deviations_list = list(baseline_deviations)
    append_harvest_demotion_deviations(verification, baseline_deviations_list)
    prose_exit, prose_claims_pytest = extract_prose_test_claim(text)
    discrepancy = annotate_test_observation_discrepancy(
        prose_claim_exit=prose_exit,
        prose_claims_pytest=prose_claims_pytest,
        verification=verification,
    )
    if discrepancy:
        baseline_deviations_list.append(discrepancy)
    baseline_deviations = baseline_deviations_list
    return verification, baseline_deviations
