"""Exit-class mapping for work_outcome grade (arc 7190)."""

from __future__ import annotations

from implement_admission.closeout_models import (
    Verification,
    derived_gate_verification,
    observed_process_verification,
    unattributed_process_verification,
)

from services.git_integration_worker.cursor_sdk_exit_interpretation import (
    interpret_exit,
    parse_accept_exits,
)


def test_pytest_usage_error_is_could_not_run_not_failed() -> None:
    row = observed_process_verification(
        command="pytest -q no_such_file.py",
        exit_code=4,
        invocation_id="test:usage",
    )
    assert interpret_exit(row) == "could_not_run"


def test_pytest_no_tests_collected_is_vacuous() -> None:
    row = observed_process_verification(
        command="pytest -q empty_dir",
        exit_code=5,
        invocation_id="test:vacuous",
    )
    assert interpret_exit(row) == "vacuous"


def test_pytest_failed_tests_are_failed() -> None:
    row = observed_process_verification(
        command="pytest -q suite.py",
        exit_code=1,
        invocation_id="test:fail",
    )
    assert interpret_exit(row) == "failed"


def test_accept_exits_declaration_at_invocation_site() -> None:
    command = "pytest -q oracle.py  # accept-exits:1"
    assert parse_accept_exits(command) == frozenset({1})
    row = observed_process_verification(
        command=command,
        exit_code=1,
        invocation_id="test:oracle",
    )
    assert interpret_exit(row) == "accepted"


def test_accept_exits_env_form() -> None:
    row = observed_process_verification(
        command="ACCEPT_EXITS=1 pytest -q oracle.py",
        exit_code=1,
        invocation_id="test:oracle-env",
    )
    assert interpret_exit(row) == "accepted"


def test_undeclared_nonzero_generic_is_uninterpreted() -> None:
    row = Verification(command="legacy probe", exit_code=1)
    assert interpret_exit(row) == "uninterpreted"


def test_unattributed_register_wins_over_accept_exits() -> None:
    row = unattributed_process_verification(
        command="pytest -q foo.py; echo done  # accept-exits:0",
        exit_code=0,
        invocation_id="test:compound",
    )
    assert interpret_exit(row) == "unattributed"


def test_ruff_findings_are_failed() -> None:
    row = observed_process_verification(
        command="ruff check 1 touched files",
        exit_code=1,
        invocation_id="lint:ruff",
    )
    assert interpret_exit(row) == "failed"


def test_gate_d_boolean_one_is_failed() -> None:
    row = derived_gate_verification(
        command="gate_d:passed",
        exit_code=1,
        basis="gate_d_boolean_pass",
        invocation_id="gate_d:fail",
    )
    assert interpret_exit(row) == "failed"


def test_unknown_pytest_exit_degrades_uninterpreted() -> None:
    row = observed_process_verification(
        command="pytest -q suite.py",
        exit_code=99,
        invocation_id="test:opaque",
    )
    assert interpret_exit(row) == "uninterpreted"
