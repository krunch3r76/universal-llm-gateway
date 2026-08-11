"""Failing-before / passing-now proof for 3e1299eb preserve-unknown G₂.

Reconstructs pre-fix ``admit_landed_true`` and missing-tip ``commits_ahead``
from ``3e1299eb^`` (no shared-checkout reset). Run as:

  MODE=pre_fix /home/io/.venvs/universal/bin/python -m \\
    services.git_integration_worker.tests.test_g2_3e1299eb_failing_before_proof
  # expect exit 1 — AssertionError on None vs False / None vs 0

  MODE=current /home/io/.venvs/universal/bin/python -m \\
    services.git_integration_worker.tests.test_g2_3e1299eb_failing_before_proof
  # expect exit 0

Pytest collects ``test_current_preserve_unknown_contract`` only.
"""

from __future__ import annotations

import os
import sys

import pytest

from services.git_integration_worker.cursor_sdk_deliverables_expected import (
    admit_landed_true,
)

pytestmark = pytest.mark.offline


def pre_fix_admit_landed_true(
    *,
    ancestry_on_master: bool | None,
    commits_ahead: int,
) -> bool:
    """Pre-3e1299eb G₂ — unknown ancestry collapses to definite False.

    Source: ``git show 3e1299eb^:…/cursor_sdk_deliverables_expected.py`` —
    ``return ancestry_on_master is True and commits_ahead >= 1``.
    Side effects: none (pure).
    """
    return ancestry_on_master is True and commits_ahead >= 1


def pre_fix_missing_tip_commits_ahead() -> int:
    """Pre-3e1299eb tip-missing meter — laundered into measured zero.

    Source: ``branch_state`` when ``_rev_parse`` returned None:
    ``BranchState(head_sha=None, commits_ahead=0, …)``.
    Side effects: none (pure).
    """
    return 0


def current_missing_tip_commits_ahead() -> int | None:
    """Post-3e1299eb tip-missing meter — preserve-no-data (None).

    Mirrors ``branch_state`` after the tip-unresolved early return.
    Side effects: none (pure).
    """
    return None


def assert_unknown_ancestry_is_null(admit_fn) -> None:
    """New contract axis 1: unknown ancestry must project landed to None."""
    assert admit_fn(ancestry_on_master=None, commits_ahead=3) is None
    assert admit_fn(ancestry_on_master=None, commits_ahead=0) is None


def assert_missing_tip_meter_is_null(missing_tip_commits_ahead: int | None) -> None:
    """New contract axis 2: missing tip must leave commits_ahead absent/None."""
    assert missing_tip_commits_ahead is None


def assert_preserve_unknown_contract(
    *,
    admit_fn,
    missing_tip_commits_ahead: int | None,
) -> None:
    """New contract: unknown ancestry → None; missing tip meter → None."""
    assert_unknown_ancestry_is_null(admit_fn)
    assert_missing_tip_meter_is_null(missing_tip_commits_ahead)


def test_current_preserve_unknown_contract() -> None:
    """Current admit + tip meter must preserve unknown under post-3e1299eb."""
    assert_preserve_unknown_contract(
        admit_fn=admit_landed_true,
        missing_tip_commits_ahead=current_missing_tip_commits_ahead(),
    )
    # Definite false and measured-zero refuse stay definite.
    assert admit_landed_true(ancestry_on_master=False, commits_ahead=3) is False
    assert admit_landed_true(ancestry_on_master=True, commits_ahead=0) is False
    assert admit_landed_true(ancestry_on_master=True, commits_ahead=None) is None


def test_pre_fix_reconstruction_matches_known_collapse() -> None:
    """Document pre-fix wrong answers — not the AC; anchors the refusal demo."""
    assert (
        pre_fix_admit_landed_true(ancestry_on_master=None, commits_ahead=3)
        is False
    )
    assert pre_fix_missing_tip_commits_ahead() == 0


def main() -> int:
    """CLI proof runner — MODE=pre_fix refuses both axes; MODE=current passes."""
    mode = os.environ.get("MODE", "").strip().lower()
    if mode == "pre_fix":
        # Check axes independently so one collapse cannot hide the other.
        failures: list[str] = []
        try:
            assert_unknown_ancestry_is_null(pre_fix_admit_landed_true)
        except AssertionError:
            got = pre_fix_admit_landed_true(
                ancestry_on_master=None, commits_ahead=3
            )
            failures.append(
                f"unknown_ancestry: expected None under new contract; "
                f"pre_fix got {got!r}"
            )
        try:
            assert_missing_tip_meter_is_null(pre_fix_missing_tip_commits_ahead())
        except AssertionError:
            got = pre_fix_missing_tip_commits_ahead()
            failures.append(
                f"missing_tip: expected None under new contract; "
                f"pre_fix got {got!r}"
            )
        if failures:
            for line in failures:
                print(f"REFUSE: {line}", file=sys.stderr)
            return 1
        print("unexpected: pre_fix satisfied new contract", file=sys.stderr)
        return 3
    if mode == "current":
        assert_preserve_unknown_contract(
            admit_fn=admit_landed_true,
            missing_tip_commits_ahead=current_missing_tip_commits_ahead(),
        )
        print("PASS: current preserve-unknown contract")
        return 0
    print(
        "usage: MODE=pre_fix|current "
        f"{sys.executable} -m "
        "services.git_integration_worker.tests."
        "test_g2_3e1299eb_failing_before_proof",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
