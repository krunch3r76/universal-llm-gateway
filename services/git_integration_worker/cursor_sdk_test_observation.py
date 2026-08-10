"""Harvest pytest-class process exits from stream ToolCallObservation into verification[].

Semantics: ``TEST_OBSERVATION_SEMANTICS`` — absence of an observed test sibling does
**not** earn "no tests ran" under harvest-only path coverage (7065#162 /
``presence_legible_absence_not``). Gate-D rows stay ``derived``; this module adds
optional ``observed`` siblings when shell / quality_gate tool results carry exits.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence

from implement_admission.closeout_models import (
    Verification,
    observed_process_verification,
)

from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)
from services.git_integration_worker.cursor_sdk_tool_result import unwrap_tool_result

TEST_OBSERVATION_SEMANTICS = "presence_legible_absence_not"

_CONTAMINATED_AGREEMENT_MARKER = (
    "test_claim@§2 pytest success without pytest witness sibling"
)


def is_pytest_command(command: str) -> bool:
    """True when *command* is a pytest-class shell invocation (hermetic token match)."""
    cmd = command.strip()
    if not cmd:
        return False
    lower = cmd.lower()
    if "python -m pytest" in lower or "python3 -m pytest" in lower:
        return True
    if "-m pytest" in lower:
        return True
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    if not tokens:
        return False
    head = tokens[0]
    return head == "pytest" or head.endswith("/pytest")


def is_pytest_witness(row: Verification) -> bool:
    """True only for observed pytest-class verification rows — never Gate-D or lint."""
    if row.exit_code_register != "observed":
        return False
    command = row.command
    if command.startswith("gate_d:"):
        return False
    lower = command.lower()
    if lower.startswith("ruff") or "ruff check" in lower:
        return False
    if lower.startswith("lint") or "lint-skip" in lower:
        return False
    return is_pytest_command(command)


def _coerce_exit_code(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def _is_shell_tool(tool_name: str) -> bool:
    return (tool_name or "").casefold() == "shell"


def _is_quality_gate_tool(tool_name: str, args: Mapping[str, object] | None) -> bool:
    if (tool_name or "").casefold() == "quality_gate":
        return True
    if not isinstance(args, Mapping):
        return False
    for key in ("toolName", "tool", "name"):
        candidate = args.get(key)
        if isinstance(candidate, str) and candidate.casefold() == "quality_gate":
            return True
    nested = args.get("args")
    if isinstance(nested, Mapping):
        for key in ("toolName", "tool", "name"):
            candidate = nested.get(key)
            if isinstance(candidate, str) and candidate.casefold() == "quality_gate":
                return True
    return False


def _command_from_observation(obs: ToolCallObservation) -> str | None:
    args = obs.args
    if not isinstance(args, Mapping):
        return None
    command = args.get("command")
    if isinstance(command, str) and command.strip():
        return command.strip()
    nested = args.get("args")
    if isinstance(nested, Mapping):
        nested_cmd = nested.get("command")
        if isinstance(nested_cmd, str) and nested_cmd.strip():
            return nested_cmd.strip()
    return None


def _shell_exit_code(obs: ToolCallObservation) -> int | None:
    raw = obs.result_body if obs.result_body is not None else obs.result
    if not isinstance(raw, Mapping):
        return None
    if raw.get("status") == "error":
        err = raw.get("error")
        if isinstance(err, Mapping):
            return _coerce_exit_code(err.get("exitCode") or err.get("exit_code"))
        return None
    value = raw.get("value")
    if isinstance(value, Mapping):
        return _coerce_exit_code(value.get("exitCode"))
    return None


def _quality_gate_test_exit(obs: ToolCallObservation) -> tuple[int | None, str | None]:
    payload = unwrap_tool_result(obs.result_body if obs.result_body is not None else obs.result)
    if not isinstance(payload, Mapping):
        return None, None
    tests = payload.get("tests")
    if not isinstance(tests, Mapping):
        return None, None
    output = str(tests.get("output") or "")
    if "skipped" in output.lower():
        return None, None
    passed = tests.get("passed")
    if not isinstance(passed, bool):
        return None, None
    command = "quality_gate:offline_tests"
    return (0 if passed else 1), command


def _harvest_shell_pytest(obs: ToolCallObservation) -> Verification | None:
    if not _is_shell_tool(obs.tool_name):
        return None
    command = _command_from_observation(obs)
    if not command or not is_pytest_command(command):
        return None
    exit_code = _shell_exit_code(obs)
    if exit_code is None:
        return None
    invocation_id = f"test:{obs.call_id}" if obs.call_id else None
    return observed_process_verification(
        command=command,
        exit_code=exit_code,
        invocation_id=invocation_id,
        basis="shell_tool_result.exitCode",
    )


def _harvest_quality_gate(obs: ToolCallObservation) -> Verification | None:
    if not _is_quality_gate_tool(obs.tool_name, obs.args if isinstance(obs.args, Mapping) else None):
        return None
    exit_code, command = _quality_gate_test_exit(obs)
    if exit_code is None or command is None:
        return None
    invocation_id = f"test:{obs.call_id}" if obs.call_id else None
    return observed_process_verification(
        command=command,
        exit_code=exit_code,
        invocation_id=invocation_id,
        basis="subprocess.run.returncode",
    )


def harvest_test_verifications(
    tool_calls: Sequence[ToolCallObservation],
) -> list[Verification]:
    """Emit one observed sibling per harvestable pytest-class / quality_gate tool call."""
    rows: list[Verification] = []
    for obs in tool_calls:
        row = _harvest_shell_pytest(obs) or _harvest_quality_gate(obs)
        if row is not None:
            rows.append(row)
    return rows


def annotate_test_observation_discrepancy(
    *,
    prose_claim_exit: int | None,
    prose_claims_pytest: bool,
    verification: Sequence[Verification],
) -> str | None:
    """§2 prose vs harvested test witness — three specimen classes (7070 / 7065 arc).

    - independent agreement → ``None`` (prose exit matches observed sibling, or no claim)
    - contaminated agreement → marker when prose claims pytest success without sibling
    - independent disagreement → marker when prose exit ≠ observed sibling exit
    """
    pytest_siblings = [row for row in verification if is_pytest_witness(row)]

    if prose_claims_pytest and prose_claim_exit == 0 and not pytest_siblings:
        return _CONTAMINATED_AGREEMENT_MARKER

    if not pytest_siblings or prose_claim_exit is None:
        return None

    sibling_exit = pytest_siblings[-1].exit_code
    if prose_claim_exit == sibling_exit:
        return None
    return (
        f"test_claim@§2 exit {prose_claim_exit} "
        f"while verification observed {sibling_exit}"
    )


__all__ = [
    "TEST_OBSERVATION_SEMANTICS",
    "annotate_test_observation_discrepancy",
    "harvest_test_verifications",
    "is_pytest_command",
    "is_pytest_witness",
]
