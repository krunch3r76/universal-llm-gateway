"""Harvest pytest-class verification siblings from stream tool calls (7065 arc)."""

from __future__ import annotations

from implement_admission.closeout_models import (
    Verification,
    derived_gate_verification,
    observed_process_verification,
)

from services.git_integration_worker.cursor_sdk_capture_status import (
    verification_all_pass,
)
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)
from services.git_integration_worker.cursor_sdk_test_observation import (
    TEST_OBSERVATION_SEMANTICS,
    _declared_check_exit_from_streams,
    annotate_test_observation_discrepancy,
    harvest_test_verifications,
    is_proven_simple_pytest_command,
    is_pytest_command,
    is_pytest_witness,
    wrapper_exit_demotion_deviation,
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


def test_harvest_keeps_stdout_tail_when_prefix_exceeds_budget() -> None:
    """Git-diff prefix must not eat the pytest summary (specimen 13015 row 4)."""
    from services.git_integration_worker.cursor_sdk_test_observation import (
        _RETAIN_CHARS,
    )

    tail = "2 passed in 0.01s\n"
    stdout = ("diff --git a/x b/x\n" + ("x" * (_RETAIN_CHARS + 40))) + tail
    obs = ToolCallObservation(
        call_id="call-tail",
        tool_name="shell",
        status="completed",
        arg_bytes=1,
        result_bytes=1,
        truncated_fields=(),
        args={"command": "pytest -q services/foo/test_bar.py"},
        result={
            "status": "success",
            "value": {"stdout": stdout, "stderr": "", "exitCode": 0},
        },
    )
    rows = harvest_test_verifications((obs,))
    assert len(rows) == 1
    row = rows[0]
    assert row.exit_code == 0
    assert row.exit_code_register == "observed"
    assert row.output_truncated is True
    assert row.stdout is not None
    assert row.stdout.startswith("...[truncated]\n")
    assert row.stdout.endswith(tail)


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
    assert row.wrapper_exit_code is None
    assert row.basis == "shell_tool_result.exitCode"
    assert row.stdout == "ok"
    assert row.stderr == ""
    assert row.invocation_id == "test:call-pytest-1"
    assert is_pytest_command(row.command)


def test_harvest_emits_unobserved_when_exit_missing() -> None:
    """AC2/AC4 — no-integer path emits a row; the row exists and blocks all_pass."""
    obs = _shell_obs(
        call_id="call-no-exit",
        command="pytest -q foo.py",
        exit_code=None,
    )
    rows = harvest_test_verifications((obs,))
    assert len(rows) == 1
    row = rows[0]
    assert row.exit_code is None
    assert row.exit_code_register == "unobserved"
    assert row.wrapper_exit_code is None
    assert row.basis == "shell_tool_result.exitCode:unobserved"
    assert row.invocation_id == "test:call-no-exit"
    lint = observed_process_verification(
        command="ruff check 2 touched files",
        exit_code=0,
        invocation_id="lint:unobserved",
        basis="subprocess.run.returncode",
    )
    assert verification_all_pass([row]) is False
    assert verification_all_pass([lint, row]) is False


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
    "call-681ca700-785c-4ece-ae96-d9f27701fb74-20\nfc_ovfiK6F-6SkKZu-14936c94-aws_ue1_2"
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


def test_specimen_auto_e93f739c279c_harvests_observed_from_pytest_exit_stdout() -> None:
    """Replay live e93f shell: PYTEST_EXIT in stdout names the process exit."""
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
    assert row.wrapper_exit_code is None
    assert row.basis == "shell_stdout.PYTEST_EXIT"
    assert row.stdout is not None and "10 passed" in row.stdout
    assert row.stderr == ""
    assert is_pytest_witness(row) is True
    assert row.invocation_id == f"test:{_E93F_CALL_ID}"


# False-positive specimens from auto-6281707f8c76 machine verification[] —
# neither shell ran pytest; both were over-emitted as observed test siblings.
_SPECIMEN_628_RG_CALL_ID = (
    "call-66c7318a-b6d3-4cf3-ab44-770f59fe80dd-11\nfc_ovfqB8U-6SkKZu-d45d5547-aws_ue1_3"
)
_SPECIMEN_628_RG_COMMAND = (
    "rg -l \"harvest\" services/git_integration_worker --glob '*.py' | head -40; "
    'rg -n "test_prepare_closeout_delivery_implement_clean_complete|'
    "test_closeout_raw_shell_outside_repo_falsifier\" -g '*.py' "
    "--glob '!**/node_modules/**' | head -40; "
    'rg -n "verification\\[\\]|harvest.*pytest|pytest.*harvest" '
    "services/git_integration_worker -g '*.py' | head -50"
)

_SPECIMEN_628_HEREDOC_CALL_ID = (
    "call-74910aaf-e715-43b5-86b2-026cdcb70240-38\nfc_ovfrFBC-6SkKZu-fa38b2a5-aws_ue1_0"
)
_SPECIMEN_628_HEREDOC_COMMAND = """OUT=/tmp/verify-6655-both-directions-bisect.md
cat > \"$OUT\" << 'EOF'
# Verify — arc 6655 closeout envelope honesty (both directions + bisect)

### Real harvest pytest run (direction A)

Command (live checkout HEAD=291faef6):

```bash
python -m pytest \\
  services/git_integration_worker/tests/test_cursor_sdk_test_observation.py \\
  services/git_integration_worker/tests/test_cursor_sdk_harvest_live_shape.py \\
  -q --tb=line
```

Verbatim result:

```text
16 passed in 0.19s
HARVEST_EXIT=0
```
EOF
wc -c \"$OUT\"
"""


def test_specimen_auto_6281707f8c76_rg_shell_not_emitted() -> None:
    """Replay receipt entry #1 — rg-only shell must NOT mint a test sibling."""
    assert is_pytest_command(_SPECIMEN_628_RG_COMMAND) is False
    obs = _shell_obs(
        call_id=_SPECIMEN_628_RG_CALL_ID,
        command=_SPECIMEN_628_RG_COMMAND,
        exit_code=0,
    )
    assert harvest_test_verifications((obs,)) == []


def test_specimen_auto_6281707f8c76_heredoc_prose_not_emitted() -> None:
    """Replay receipt entry #5 — heredoc quoting pytest prose must NOT emit."""
    assert is_pytest_command(_SPECIMEN_628_HEREDOC_COMMAND) is False
    obs = _shell_obs(
        call_id=_SPECIMEN_628_HEREDOC_CALL_ID,
        command=_SPECIMEN_628_HEREDOC_COMMAND,
        exit_code=0,
    )
    assert harvest_test_verifications((obs,)) == []


_SPECIMEN_A_CALL_ID = "call-specimen-a-46c3"
_SPECIMEN_A_COMMAND = (
    "cd /mnt/torus/projects/universal-llm-gateway && "
    "/home/io/.venvs/universal/bin/python -m pytest "
    "libs/implement_admission services/git_integration_worker/tests --tb=no -q "
    "2>&1 | tee /tmp/ambient-suite-6655.txt; "
    'echo "SUITE_EXIT:${PIPESTATUS[0]}"'
)


def test_harvest_specimen_a_compound_echo_without_stdout_marker_stays_unattributed() -> None:
    """RED — pipeline+tee+echo with no SUITE_EXIT line cannot name pytest exit."""
    obs = _shell_obs(
        call_id=_SPECIMEN_A_CALL_ID,
        command=_SPECIMEN_A_COMMAND,
        exit_code=0,
    )
    rows = harvest_test_verifications((obs,))
    assert len(rows) == 1
    row = rows[0]
    assert row.exit_code_register == "unattributed"
    assert row.exit_code is None
    assert row.wrapper_exit_code == 0
    assert row.basis == "shell_tool_result.exitCode:unattributed"
    assert is_proven_simple_pytest_command(_SPECIMEN_A_COMMAND) is False
    assert wrapper_exit_demotion_deviation(row) == (
        f"wrapper_exit_demoted:{_SPECIMEN_A_CALL_ID}"
    )
    lint = observed_process_verification(
        command="ruff check 2 touched files",
        exit_code=0,
        invocation_id="lint:specimen-a",
        basis="subprocess.run.returncode",
    )
    assert verification_all_pass([lint, row]) is False


_SPECIMEN_13147_CALL_ID = "call-5a19dac8-4e1a-4964-9aad-26c540a228dd-114"
_SPECIMEN_13147_COMMAND = (
    "/home/io/.venvs/universal/bin/python -m pytest "
    "services/git_integration_worker/tests/test_cursor_sdk_test_observation.py -q; "
    'echo "PYTEST_EXIT:$?"'
)
_SPECIMEN_13147_STDOUT = "5 passed in 0.94s\nPYTEST_EXIT:0"


def test_declared_check_exit_from_streams_pytest_exit_colon_matches_equals() -> None:
    """AC1/AC2 — PYTEST_EXIT:0 and PYTEST_EXIT=0 name the same process exit."""
    command = "pytest -q foo.py; echo PYTEST_EXIT:$?"
    colon = _declared_check_exit_from_streams(command, "summary\nPYTEST_EXIT:0\n", None)
    equals = _declared_check_exit_from_streams(command, "summary\nPYTEST_EXIT=0\n", None)
    assert colon == (0, "PYTEST_EXIT")
    assert equals == (0, "PYTEST_EXIT")


def test_harvest_specimen_13147_pytest_exit_colon_stdout_is_observed() -> None:
    """AC2/AC3 — replay arc 13147 stdout; colon delimiter must yield observed register."""
    obs = _shell_obs(
        call_id=_SPECIMEN_13147_CALL_ID,
        command=_SPECIMEN_13147_COMMAND,
        exit_code=0,
    )
    obs.result["value"]["stdout"] = _SPECIMEN_13147_STDOUT
    rows = harvest_test_verifications((obs,))
    assert len(rows) == 1
    row = rows[0]
    assert row.exit_code_register == "observed"
    assert row.exit_code == 0
    assert row.basis == "shell_stdout.PYTEST_EXIT"


def test_harvest_specimen_a_suite_exit_stdout_is_observed() -> None:
    """GREEN — SUITE_EXIT in stdout attributes the pytest process exit."""
    obs = _shell_obs(
        call_id=_SPECIMEN_A_CALL_ID,
        command=_SPECIMEN_A_COMMAND,
        exit_code=0,
    )
    obs.result["value"]["stdout"] = "62 passed in 1.2s\nSUITE_EXIT:0\n"
    rows = harvest_test_verifications((obs,))
    row = rows[0]
    assert row.exit_code_register == "observed"
    assert row.exit_code == 0
    assert row.basis == "shell_stdout.SUITE_EXIT"


def test_harvest_compound_red_exit_echo_stdout_is_observed() -> None:
    """GREEN — RED_exit echo after pytest names the probe exit (auto-363018bf shape)."""
    command = (
        "pytest -q services/git_integration_worker/tests/test_x.py; "
        'EC=$?; echo "RED_exit:$EC"'
    )
    obs = _shell_obs(call_id="call-red-exit-echo", command=command, exit_code=0)
    obs.result["value"]["stdout"] = "1 failed\nRED_exit:1\n"
    rows = harvest_test_verifications((obs,))
    row = rows[0]
    assert row.exit_code_register == "observed"
    assert row.exit_code == 1
    assert row.basis == "shell_stdout.RED_exit"


def test_multiline_pytest_exit_is_observed_without_stdout_echo() -> None:
    """Backslash-continued pytest keeps the shell exit as the process exit.

    Seats were echoing ``exit=$?`` because a wrapped invocation was demoted to
    ``exit_code=None`` / ``unattributed``. The shell exit is the pytest exit
    when the continuation is still one pytest process.
    """
    command = (
        "cd /mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/lane-13141 && \\\n"
        "$HOME/.venvs/universal/bin/pytest \\\n"
        "  services/git_integration_worker/tests/test_cursor_sdk_test_observation.py \\\n"
        "  -q --tb=line"
    )
    assert is_pytest_command(command) is True
    assert is_proven_simple_pytest_command(command) is True
    obs = _shell_obs(
        call_id="call-multiline-pytest-no-echo",
        command=command,
        exit_code=1,
    )
    rows = harvest_test_verifications((obs,))
    assert len(rows) == 1
    row = rows[0]
    assert row.exit_code == 1
    assert row.exit_code_register == "observed"
    assert row.wrapper_exit_code is None
    assert wrapper_exit_demotion_deviation(row) is None
    assert is_pytest_witness(row) is True


def test_is_proven_simple_allows_ruff_and_pytest_and_chain() -> None:
    assert is_proven_simple_pytest_command("pytest -q foo.py") is True
    assert (
        is_proven_simple_pytest_command("ruff check a.py && pytest -q foo.py") is True
    )
    assert is_proven_simple_pytest_command("pytest -q foo.py; echo done") is False
    assert (
        is_proven_simple_pytest_command(
            "pytest -q foo.py | tee /tmp/out.txt; echo done"
        )
        is False
    )


# Specimen auto-5dccdbd360d0 / thread 13032 turn 6 (quoted venv pytest path).
_SPECIMEN_13032_COMMAND = (
    "cd /mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/lane-13032 && "
    '"$HOME/.venvs/universal/bin/ruff" check '
    "services/git_integration_worker/tests/test_cursor_sdk_capture_status.py && "
    '"$HOME/.venvs/universal/bin/pytest" -q '
    "services/git_integration_worker/tests/test_cursor_sdk_capture_status.py"
)
_SPECIMEN_13032_STDOUT = (
    "All checks passed!\n"
    "..............................................................           [100%]\n"
    "62 passed in 1.25s\n"
)
_SPECIMEN_13032_RESULT = {
    "status": "success",
    "value": {
        "exitCode": 0,
        "signal": "",
        "stdout": _SPECIMEN_13032_STDOUT,
        "stderr": "",
        "executionTime": 2387,
    },
}


def test_specimen_13032_quoted_pytest_path_harvested_observed() -> None:
    """Quoted ``$HOME/.venvs/.../pytest`` must not vanish during literal strip."""
    from services.git_integration_worker.cursor_sdk_test_observation import (
        _strip_shell_literals,
    )

    assert is_pytest_command(_SPECIMEN_13032_COMMAND)
    assert is_proven_simple_pytest_command(_SPECIMEN_13032_COMMAND)
    stripped = _strip_shell_literals(_SPECIMEN_13032_COMMAND)
    assert "$HOME/.venvs/universal/bin/pytest" in stripped
    obs = ToolCallObservation(
        call_id="tool_24c24c1b-ed11-4ea0-af29-b1d8b8443cd",
        tool_name="shell",
        status="completed",
        arg_bytes=340,
        result_bytes=233,
        truncated_fields=(),
        args={"command": _SPECIMEN_13032_COMMAND, "timeout": 120000},
        result=_SPECIMEN_13032_RESULT,
        result_body=_SPECIMEN_13032_RESULT,
        result_body_status="present",
    )
    rows = harvest_test_verifications((obs,))
    assert len(rows) == 1
    row = rows[0]
    assert row.exit_code_register == "observed"
    assert row.exit_code == 0
    assert row.stdout is not None and "62 passed" in row.stdout


def test_quoted_pytest_with_k_expression_literal_survives_strip() -> None:
    from services.git_integration_worker.cursor_sdk_test_observation import (
        _strip_shell_literals,
    )

    command = '"$VENV/bin/pytest" -k "not slow" path'
    assert is_pytest_command(command)
    assert is_proven_simple_pytest_command(command)
    stripped = _strip_shell_literals(command)
    assert "not slow" in stripped
    obs = _shell_obs(call_id="call-k-slow", command=command, exit_code=0)
    rows = harvest_test_verifications((obs,))
    assert len(rows) == 1
    assert rows[0].command == command
    assert '-k "not slow"' in rows[0].command


# Specimen agent-bus:13042 / friction a:36693 — env prefix + unquoted venv pytest + tail pipe.
_SPECIMEN_13042_COMMAND = (
    "cd /mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/lane-13042 && "
    "env -u CURSOR_SDK_DISPATCH_LEDGER $HOME/.venvs/universal/bin/pytest "
    "libs/claude_bundles/test_cdp_registry_document.py "
    "libs/claude_bundles/test_cdp_registry_remote_read.py -q --tb=short 2>&1 | tail -20"
)
_SPECIMEN_13042_QUOTED_PYTEST_COMMAND = (
    "cd /mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/lane-13042 && "
    "env -u CURSOR_SDK_DISPATCH_LEDGER "
    '"$HOME/.venvs/universal/bin/pytest" '
    "libs/claude_bundles/test_cdp_registry_document.py "
    "libs/claude_bundles/test_cdp_registry_remote_read.py -q --tb=short 2>&1 | tail -20"
)
_SPECIMEN_13042_STDOUT = (
    "........................................                                 [100%]\n"
    "40 passed in 1.02s\n"
)


def test_specimen_13042_unquoted_venv_env_prefix_tail_harvested_observed() -> None:
    """Miss specimen — unquoted ``$HOME/.venvs/.../pytest`` with ``env -u`` and ``| tail``."""
    assert is_pytest_command(_SPECIMEN_13042_COMMAND)
    assert is_proven_simple_pytest_command(_SPECIMEN_13042_COMMAND)
    obs = ToolCallObservation(
        call_id="call-specimen-13042-unquoted",
        tool_name="shell",
        status="completed",
        arg_bytes=400,
        result_bytes=200,
        truncated_fields=(),
        args={"command": _SPECIMEN_13042_COMMAND},
        result={
            "status": "success",
            "value": {
                "exitCode": 0,
                "stdout": _SPECIMEN_13042_STDOUT,
                "stderr": "",
            },
        },
    )
    rows = harvest_test_verifications((obs,))
    assert len(rows) == 1
    row = rows[0]
    assert row.exit_code_register == "observed"
    assert row.exit_code == 0
    assert "libs/claude_bundles/test_cdp_registry_document.py" in row.command
    assert "libs/claude_bundles/test_cdp_registry_remote_read.py" in row.command
    assert is_pytest_witness(row) is True
    assert row.stdout is not None and "40 passed" in row.stdout


def test_specimen_13042_quoted_pytest_env_prefix_tail_harvested_observed() -> None:
    """Same specimen shape with quoted venv pytest path."""
    assert is_pytest_command(_SPECIMEN_13042_QUOTED_PYTEST_COMMAND)
    assert is_proven_simple_pytest_command(_SPECIMEN_13042_QUOTED_PYTEST_COMMAND)
    obs = _shell_obs(
        call_id="call-specimen-13042-quoted",
        command=_SPECIMEN_13042_QUOTED_PYTEST_COMMAND,
        exit_code=0,
    )
    rows = harvest_test_verifications((obs,))
    assert len(rows) == 1
    row = rows[0]
    assert row.exit_code_register == "observed"
    assert is_pytest_witness(row) is True
    assert "test_cdp_registry_document.py" in row.command


def test_wrapper_unavailable_harvest_emits_null_not_zero() -> None:
    """AC2 — compound wrapper cannot be recorded as process exit 0."""
    obs = _shell_obs(
        call_id="call-wrapper-unavailable",
        command=_SPECIMEN_A_COMMAND,
        exit_code=0,
    )
    rows = harvest_test_verifications((obs,))
    assert len(rows) == 1
    row = rows[0]
    assert row.exit_code is None
    assert row.exit_code != 0
    assert row.wrapper_exit_code == 0
    assert row.exit_code_register == "unattributed"
    assert row.stdout == "ok"
    assert row.stderr == ""
