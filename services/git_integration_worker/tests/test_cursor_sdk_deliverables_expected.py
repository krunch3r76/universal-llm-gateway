"""Commissioner deliverables_expected widen + G₂ landed bind (success-shaped-silence)."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_sdk_deliverables_expected import (
    admit_landed_true,
    compute_deliverables_expected,
    extract_evidence_required_uris,
    packet_names_deliverable_obligation,
)
from services.git_integration_worker.cursor_sdk_light_bounded_capture import (
    extract_instructed_paths,
)

pytestmark = pytest.mark.offline


def test_extract_instructed_paths_does_not_lift_bare_evidence_required() -> None:
    """Operator bind check: gap is real — evidence_required alone is not imperative."""
    prose = (
        "evidence_required: cortex://notes/system/threads/success-shaped-silence/"
        "r1-r2-term-bind.md\n"
        "contract: investigate\n"
    )
    assert extract_instructed_paths(prose) == ()
    assert extract_evidence_required_uris(prose) == (
        "cortex://notes/system/threads/success-shaped-silence/r1-r2-term-bind.md",
    )


def test_compute_deliverables_expected_true_on_evidence_required() -> None:
    prose = (
        "TYPE: DIRECTIVE\ncontract: investigate\n"
        "evidence_required: cortex://notes/system/threads/foo.md · sha256:abc\n"
    )
    assert packet_names_deliverable_obligation(prose) is True
    assert (
        compute_deliverables_expected(
            contract="investigate",
            instruction_text=prose,
            light_bounded_expected_paths=(),
        )
        is True
    )


def test_compute_deliverables_expected_true_on_files_expected_imperative() -> None:
    prose = "files_expected:\n- services/git_integration_worker/cursor_sdk_closeout.py\n"
    assert extract_instructed_paths(prose)
    assert (
        compute_deliverables_expected(
            contract="investigate",
            instruction_text=prose,
        )
        is True
    )


def test_compute_deliverables_expected_false_for_bare_consult() -> None:
    prose = "contract: consult\nAnswer the architecture question; no durable write.\n"
    assert (
        compute_deliverables_expected(
            contract="consult",
            instruction_text=prose,
        )
        is False
    )


def test_g2_trace_ii_auto_625a11ce0892_refuse_landed_at_commits_ahead_zero() -> None:
    """(ii) G₂ REFUSE — landed:true forbidden when commits_ahead=0 (head==branch_point)."""
    assert (
        admit_landed_true(ancestry_on_master=True, commits_ahead=0) is False
    )
    assert admit_landed_true(ancestry_on_master=True, commits_ahead=1) is True
    assert admit_landed_true(ancestry_on_master=False, commits_ahead=2) is False


def test_g2_unknown_ancestry_emits_landed_null_not_false() -> None:
    """Unknown ancestry must not collapse to structured landed:false (arc 6655)."""
    assert admit_landed_true(ancestry_on_master=None, commits_ahead=3) is None
    assert admit_landed_true(ancestry_on_master=None, commits_ahead=0) is None
    assert admit_landed_true(ancestry_on_master=False, commits_ahead=3) is False
    assert admit_landed_true(ancestry_on_master=True, commits_ahead=None) is None
