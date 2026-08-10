"""Harvest pytest-class verification siblings from stream tool calls (7065 arc)."""

from __future__ import annotations

from implement_admission.closeout_models import (
    Verification,
    derived_gate_verification,
    observed_process_verification,
)

from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)
from services.git_integration_worker.cursor_sdk_test_observation import (
    TEST_OBSERVATION_SEMANTICS,
    annotate_test_observation_discrepancy,
    harvest_test_verifications,
    is_pytest_command,
    is_pytest_witness,
)


def _shell_obs(
    *,
    call_id: str,
    command: str,
    exit_code: int | None,
    status: str = "completed",
) -> ToolCallObservation:
    result = None
    if exit_code is not None:
        result = {
            "status": "success",
            "value": {"stdout": "ok", "stderr": "", "exitCode": exit_code},
        }
    return ToolCallObservation(
        call_id=call_id,
        tool_name="shell",
        status=status,
        arg_bytes=1,
        result_bytes=1,
        truncated_fields=(),
        args={"command": command},
        result=result,
    )


def test_is_pytest_command_tokens() -> None:
    assert is_pytest_command("pytest -q services/foo/test_bar.py")
    assert is_pytest_command("python -m pytest libs/")
    assert is_pytest_command("/home/io/.venvs/universal/bin/python -m pytest -q")
    assert is_pytest_command(
        "export PATH=/x\npytest -q services/foo/test_bar.py\necho done"
    )
    assert not is_pytest_command("ruff check foo.py")
    assert not is_pytest_command("echo pytest")
    assert not is_pytest_command("which pytest")


def test_is_pytest_witness_denies_gate_d_and_lint() -> None:
    gate_d = derived_gate_verification(
        command="gate_d:passed",
        exit_code=0,
        basis="gate_d_boolean_pass",
        invocation_id="gate_d:fixture",
    )
    lint = observed_process_verification(
        command="ruff check 2 touched files",
        exit_code=0,
        invocation_id="lint:fixture",
        basis="subprocess.run.returncode",
    )
    pytest_row = observed_process_verification(
        command="pytest -q services/git_integration_worker/tests/test_foo.py",
        exit_code=0,
        invocation_id="test:abc",
        basis="shell_tool_result.exitCode",
    )
    assert is_pytest_witness(gate_d) is False
    assert is_pytest_witness(lint) is False
    assert is_pytest_witness(pytest_row) is True


def test_harvest_emits_observed_sibling_for_pytest_shell() -> None:
    obs = _shell_obs(
        call_id="call-pytest-1",
        command="pytest -q services/git_integration_worker/tests/test_cursor_sdk_test_observation.py",
        exit_code=0,
    )
    rows = harvest_test_verifications((obs,))
    assert len(rows) == 1
    row = rows[0]
    assert row.exit_code_register == "observed"
    assert row.exit_code == 0
    assert row.basis == "shell_tool_result.exitCode"
    assert row.invocation_id == "test:call-pytest-1"
    assert is_pytest_command(row.command)


def test_harvest_skips_when_exit_missing() -> None:
    obs = _shell_obs(
        call_id="call-no-exit",
        command="pytest -q foo.py",
        exit_code=None,
    )
    assert harvest_test_verifications((obs,)) == []


def test_harvest_absent_when_no_pytest_shell() -> None:
    obs = _shell_obs(call_id="call-echo", command="echo hello", exit_code=0)
    assert harvest_test_verifications((obs,)) == []
    assert TEST_OBSERVATION_SEMANTICS == "presence_legible_absence_not"


def test_harvest_one_row_per_matching_call() -> None:
    first = _shell_obs(call_id="a", command="pytest -q a.py", exit_code=0)
    second = _shell_obs(call_id="b", command="python -m pytest b.py", exit_code=1)
    rows = harvest_test_verifications((first, second))
    assert len(rows) == 2
    assert rows[0].invocation_id == "test:a"
    assert rows[1].invocation_id == "test:b"
    assert rows[1].exit_code == 1


def test_gate_d_rows_unaffected_by_harvest_companion() -> None:
    gate_d = derived_gate_verification(
        command="gate_d:passed",
        exit_code=0,
        basis="gate_d_boolean_pass",
        invocation_id="gate_d:fixture",
    )
    obs = _shell_obs(call_id="c", command="pytest -q c.py", exit_code=0)
    verification: list[Verification] = [gate_d, *harvest_test_verifications((obs,))]
    assert verification[0].exit_code_register == "derived"
    assert verification[1].exit_code_register == "observed"


def test_specimen_independent_agreement_silent() -> None:
    verification = [
        observed_process_verification(
            command="pytest -q foo.py",
            exit_code=0,
            invocation_id="test:agree",
            basis="shell_tool_result.exitCode",
        )
    ]
    assert (
        annotate_test_observation_discrepancy(
            prose_claim_exit=0,
            prose_claims_pytest=True,
            verification=verification,
        )
        is None
    )


def test_specimen_contaminated_agreement_detected() -> None:
    marker = annotate_test_observation_discrepancy(
        prose_claim_exit=0,
        prose_claims_pytest=True,
        verification=[],
    )
    assert marker == "test_claim@§2 pytest success without pytest witness sibling"


def test_specimen_independent_disagreement_fires() -> None:
    verification = [
        observed_process_verification(
            command="pytest -q foo.py",
            exit_code=1,
            invocation_id="test:disagree",
            basis="shell_tool_result.exitCode",
        )
    ]
    marker = annotate_test_observation_discrepancy(
        prose_claim_exit=0,
        prose_claims_pytest=True,
        verification=verification,
    )
    assert marker == "test_claim@§2 exit 0 while verification observed 1"


# Live observation record from auto-e93f739c279c (frontier.sdk.worker.toolcall
# @ 2026-08-10T18:00:59.460908Z) + command from that dispatch's effects_manifest.
_E93F_CALL_ID = (
    "call-681ca700-785c-4ece-ae96-d9f27701fb74-20\n"
    "fc_ovfiK6F-6SkKZu-14936c94-aws_ue1_2"
)
_E93F_COMMAND = """# use system universal venv
export PATH="$HOME/.venvs/universal/bin:$PATH"
which python pytest ruff
cd /mnt/torus/projects/universal-llm-gateway
# Full-file gate on six touched modules
echo '=== RUFF ==='
ruff check \\
  libs/implement_admission/closeout_models.py \\
  libs/implement_admission/deliverable_verification.py \\
  services/git_integration_worker/cursor_sdk_capture_status.py \\
  services/git_integration_worker/cursor_sdk_closeout.py \\
  services/git_integration_worker/cursor_sdk_test_observation.py \\
  services/git_integration_worker/tests/test_cursor_sdk_test_observation.py
echo "RUFF_EXIT=$?"
echo '=== PYTEST ==='
pytest -q services/git_integration_worker/tests/test_cursor_sdk_test_observation.py
echo "PYTEST_EXIT=$?"
"""
_E93F_RESULT = {
    "status": "success",
    "value": {
        "exitCode": 0,
        "signal": "",
        "stdout": (
            "/home/io/.venvs/universal/bin/python\n"
            "/home/io/.venvs/universal/bin/pytest\n"
            "/home/io/.venvs/universal/bin/ruff\n"
            "=== RUFF ===\n"
            "All checks passed!\n"
            "RUFF_EXIT=0\n"
            "=== PYTEST ===\n"
            "..........                                               "
            "                [100%]\n"
            "10 passed in 0.17s\n"
            "PYTEST_EXIT=0\n"
        ),
        "stderr": "",
        "executionTime": 637,
    },
}


def test_specimen_auto_e93f739c279c_harvests_observed_test_sibling() -> None:
    """Replay live e93f shell ToolCallObservation; require observed pytest sibling."""
    obs = ToolCallObservation(
        call_id=_E93F_CALL_ID,
        tool_name="shell",
        status="completed",
        arg_bytes=800,
        result_bytes=402,
        truncated_fields=(),
        args={"command": _E93F_COMMAND},
        result=_E93F_RESULT,
        result_body=_E93F_RESULT,
        result_body_status="present",
    )
    rows = harvest_test_verifications((obs,))
    assert len(rows) == 1
    row = rows[0]
    assert row.exit_code_register == "observed"
    assert row.exit_code == 0
    assert row.basis == "shell_tool_result.exitCode"
    assert is_pytest_witness(row) is True
    assert row.invocation_id == f"test:{_E93F_CALL_ID}"
