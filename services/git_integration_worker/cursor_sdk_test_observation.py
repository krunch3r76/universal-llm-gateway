"""Harvest pytest-class process exits from stream ToolCallObservation into verification[].

Semantics: ``TEST_OBSERVATION_SEMANTICS`` — absence of an observed test sibling does
**not** earn "no tests ran" under harvest-only path coverage (7065#162 /
``presence_legible_absence_not``). Gate-D rows stay ``derived``; this module adds
optional ``observed`` siblings when shell / quality_gate tool results carry exits.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from implement_admission.closeout_models import (
    Verification,
    observed_process_verification,
    unattributed_process_verification,
    unobserved_process_verification,
)

from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)
from services.git_integration_worker.cursor_sdk_tool_result import unwrap_tool_result

TEST_OBSERVATION_SEMANTICS = "presence_legible_absence_not"

_CONTAMINATED_AGREEMENT_MARKER = (
    "test_claim@§2 pytest success without pytest witness sibling"
)

# Invoke-position pytest: start of string/line, or after shell separators.
# ``shlex`` collapses newlines, so line anchors on the raw command are required
# for compound gate shells (specimen auto-e93f739c279c). Rejects ``echo pytest``.
_PYTEST_INVOKE_RE = re.compile(
    r"(?:^|[\n;|&]|\|\||&&)\s*(?:[^\s;|&]*/)?pytest\b",
    flags=re.MULTILINE,
)

# Heredoc bodies and quoted strings mention pytest in prose / rg patterns without
# invoking it (arc-6655 auto-6281707f8c76 direction-B over-emission).
_HEREDOC_BODY_RE = re.compile(
    r"<<-?\s*(['\"]?)(\w+)\1\r?\n.*?\r?\n\2(?:\r?\n|$)",
    flags=re.DOTALL,
)
_SINGLE_QUOTED_RE = re.compile(r"'[^']*'")
_DOUBLE_QUOTED_RE = re.compile(r'"(?:\\.|[^"\\])*"')
_PIPEFAIL_RE = re.compile(r"set\s+-o\s+pipefail", flags=re.IGNORECASE)
_CHAIN_SPLIT_RE = re.compile(r"&&|\n|;")


def _chain_segments(command: str) -> list[str]:
    """Split shell script into ``;`` / ``&&`` / newline-separated segments."""
    segments: list[str] = []
    for part in _CHAIN_SPLIT_RE.split(command):
        stripped = part.strip()
        if stripped:
            segments.append(stripped)
    return segments


def is_proven_simple_pytest_command(command: str) -> bool:
    """True when shell exit can name the pytest process under test.

    Default-deny compound shapes (specimen ``auto-46c3c9c57994``):

    - **Deny** — ``pytest … | tee …; echo "SUITE_EXIT:${PIPESTATUS[0]}"`` (pipeline
      wrapper masks pytest exit; outer shell exit is the trailing ``echo``).
    - **Deny** — trailing ``; echo`` / newline-echo after pytest (outer exit is
      the echo; pytest failure is laundered). Last chain segment must invoke
      pytest (arc 7190 survival-3).
    - **Allow** — ``ruff … && pytest …`` with pytest terminal in the ``&&`` chain.
    - **Allow** — ``pytest | …`` pipeline only when ``set -o pipefail`` appears
      earlier in the raw script (pipefail binds pipeline exit to pytest).
    """
    cmd = command.strip()
    if not cmd or not is_pytest_command(cmd):
        return False

    segments = _chain_segments(_strip_shell_literals(cmd))
    if not segments or not is_pytest_command(segments[-1]):
        return False
    segment = segments[-1]

    if "|" not in segment:
        return True

    if not _PIPEFAIL_RE.search(cmd):
        return False

    head = segment.lstrip()
    lower = head.lower()
    return (
        lower.startswith("pytest")
        or "python -m pytest" in lower
        or "python3 -m pytest" in lower
        or (lower.startswith("python") and "-m pytest" in lower)
    )


def _strip_shell_literals(command: str) -> str:
    """Remove heredoc bodies and quoted strings so prose cannot fake invoke-position."""
    cleaned = _HEREDOC_BODY_RE.sub("\n", command)
    cleaned = _SINGLE_QUOTED_RE.sub(" ", cleaned)
    cleaned = _DOUBLE_QUOTED_RE.sub(" ", cleaned)
    return cleaned


def is_pytest_command(command: str) -> bool:
    """True when *command* is a pytest-class shell invocation (hermetic match).

    Matches head ``pytest``, ``*/pytest``, ``python -m pytest``, and mid-script
    invoke-position ``pytest`` after newlines / ``;`` / ``&&`` (compound gate
    shells). Does not treat ``echo pytest`` / ``which pytest`` as invocations.
    Quoted strings and heredoc bodies are stripped first so narrative / ``rg``
    pattern text cannot mint a false observed test sibling.
    """
    cmd = command.strip()
    if not cmd:
        return False
    executable = _strip_shell_literals(cmd)
    if not executable.strip():
        return False
    lower = executable.lower()
    if "python -m pytest" in lower or "python3 -m pytest" in lower:
        return True
    if "-m pytest" in lower:
        return True
    return _PYTEST_INVOKE_RE.search(executable) is not None


def is_pytest_witness(row: Verification) -> bool:
    """True only for observed pytest-class verification rows — never Gate-D.

    Compound shells that also run ``ruff check`` (specimen e93f) remain witnesses
    when ``is_pytest_command`` matches; lint-only rows fail that predicate.
    """
    if row.exit_code_register != "observed":
        return False
    if row.command.startswith("gate_d:"):
        return False
    return is_pytest_command(row.command)


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


_RETAIN_CHARS = 4000


def _retain_text(raw: object) -> tuple[str | None, bool]:
    """Cap a retained shell stream; empty/absent stays ``None`` only when missing."""
    if raw is None:
        return None, False
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    if len(text) <= _RETAIN_CHARS:
        return text, False
    return text[:_RETAIN_CHARS] + "\n...[truncated]", True


def _shell_result_map(obs: ToolCallObservation) -> Mapping[str, object] | None:
    raw = obs.result_body if obs.result_body is not None else obs.result
    if not isinstance(raw, Mapping):
        return None
    if raw.get("status") == "error":
        err = raw.get("error")
        return err if isinstance(err, Mapping) else None
    value = raw.get("value")
    return value if isinstance(value, Mapping) else None


def _shell_exit_code(obs: ToolCallObservation) -> int | None:
    payload = _shell_result_map(obs)
    if payload is None:
        return None
    raw_exit = payload.get("exitCode")
    if raw_exit is None:
        raw_exit = payload.get("exit_code")
    return _coerce_exit_code(raw_exit)


def _shell_harvest_basis(
    payload: Mapping[str, object] | None,
    *,
    unattributed: bool = False,
    unobserved: bool = False,
) -> str:
    if unobserved:
        stem = "shell_tool_result.exitCode:unobserved"
    elif unattributed:
        stem = "shell_tool_result.exitCode:unattributed"
    else:
        stem = "shell_tool_result.exitCode"
    if payload is None:
        return stem
    parts = [stem]
    if "signal" in payload:
        parts.append(f"signal={payload.get('signal', '')}")
    if "executionTime" in payload:
        parts.append(f"executionTime={payload.get('executionTime')}")
    return ";".join(parts)


def _quality_gate_test_exit(obs: ToolCallObservation) -> tuple[int | None, str | None]:
    payload = unwrap_tool_result(
        obs.result_body if obs.result_body is not None else obs.result
    )
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
    payload = _shell_result_map(obs)
    exit_code = _shell_exit_code(obs)
    invocation_id = f"test:{obs.call_id}" if obs.call_id else None
    stdout, trunc_out = _retain_text(None if payload is None else payload.get("stdout"))
    stderr, trunc_err = _retain_text(None if payload is None else payload.get("stderr"))
    if exit_code is None:
        return unobserved_process_verification(
            command=command,
            wrapper_exit_code=None,
            invocation_id=invocation_id,
            basis=_shell_harvest_basis(payload, unobserved=True),
            stdout=stdout,
            stderr=stderr,
            output_truncated=trunc_out or trunc_err,
        )
    unattributed = not is_proven_simple_pytest_command(command)
    pack = (
        unattributed_process_verification
        if unattributed
        else observed_process_verification
    )
    return pack(
        command=command,
        exit_code=exit_code,
        invocation_id=invocation_id,
        basis=_shell_harvest_basis(payload, unattributed=unattributed),
        stdout=stdout,
        stderr=stderr,
        output_truncated=trunc_out or trunc_err,
    )


def _harvest_quality_gate(obs: ToolCallObservation) -> Verification | None:
    if not _is_quality_gate_tool(
        obs.tool_name, obs.args if isinstance(obs.args, Mapping) else None
    ):
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


def extract_prose_test_claim(body: str) -> tuple[int | None, bool]:
    """Extract §2 / sidecar prose pytest exit claim when present.

    Returns ``(exit_code, claims_pytest)``. When no claim is found, returns
    ``(None, False)`` so the annotator stays silent.
    """
    text = body or ""
    lower = text.lower()
    claims_pytest = bool(
        re.search(r"\bpytest\b", lower)
        and re.search(
            r"(?:exit_code|suite_exit|pytest_exit)\s*[:=]\s*(\d+)",
            lower,
        )
    )
    if not claims_pytest:
        return None, False
    match = re.search(
        r"(?:exit_code|suite_exit|pytest_exit)\s*[:=]\s*(\d+)",
        lower,
    )
    if match is None:
        return None, True
    return int(match.group(1)), True


def wrapper_exit_demotion_deviation(row: Verification) -> str | None:
    """Map demoted harvest row to ``wrapper_exit_demoted:<call_id>`` token."""
    if row.exit_code_register != "unattributed":
        return None
    invocation_id = row.invocation_id or ""
    if not invocation_id.startswith("test:"):
        return None
    call_id = invocation_id.removeprefix("test:")
    if not call_id:
        return None
    return f"wrapper_exit_demoted:{call_id}"


def append_harvest_demotion_deviations(
    verification: Sequence[Verification],
    deviations: list[str],
) -> None:
    """Append ``wrapper_exit_demoted:<call_id>`` for each unattributed harvest row."""
    seen = set(deviations)
    for row in verification:
        token = wrapper_exit_demotion_deviation(row)
        if token and token not in seen:
            deviations.append(token)
            seen.add(token)


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
    "append_harvest_demotion_deviations",
    "extract_prose_test_claim",
    "harvest_test_verifications",
    "is_proven_simple_pytest_command",
    "is_pytest_command",
    "is_pytest_witness",
    "wrapper_exit_demotion_deviation",
]
