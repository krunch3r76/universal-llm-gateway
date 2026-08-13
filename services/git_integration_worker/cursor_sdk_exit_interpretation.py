"""Map verification exit integers onto process-outcome classes.

``work_outcome`` used to treat every nonzero ``exit_code`` as
``checks_failed``. Pytest usage errors, vacuous collection, designed-fail
probes, and real test failures therefore rendered as one word, while empty
``verification[]`` could still ship via the positive-artifact ladder.

Callers: ``cursor_sdk_capture_status`` (grade). Invariant: a class the grader
does not know degrades to ``uninterpreted`` (blocks shipped, never
``checks_failed``).
"""

from __future__ import annotations

import re
from typing import Literal

from implement_admission.closeout_models import Verification

ExitClass = Literal[
    "passed",
    "failed",
    "could_not_run",
    "vacuous",
    "accepted",
    "uninterpreted",
    "unattributed",
]

# Pytest: https://docs.pytest.org/en/latest/reference/exit-codes.html
_PYTEST_EXIT: dict[int, ExitClass] = {
    0: "passed",
    1: "failed",
    2: "could_not_run",  # interrupted
    3: "could_not_run",  # internal error
    4: "could_not_run",  # usage error
    5: "vacuous",  # no tests collected
}

# Ruff: 0 clean, 1 findings. Other codes are not a documented findings signal.
_RUFF_EXIT: dict[int, ExitClass] = {
    0: "passed",
    1: "failed",
}

_RUFF_INVOKE_RE = re.compile(
    r"(?:^|[\n;|&]|\|\||&&)\s*(?:[^\s;|&]*/)?ruff\b",
    flags=re.MULTILINE,
)

# Invocation-site declaration. Absent ⇒ a designed-fail probe cannot record as
# a passing check (structurally unrecordable as accepted).
_ACCEPT_EXITS_RE = re.compile(
    r"(?i)(?:ACCEPT_EXITS|#\s*accept[-_]exits|accept[-_]exits)\s*[=:]\s*"
    r"([0-9]+(?:\s*,\s*[0-9]+)*)"
)

_PASSING_CLASSES: frozenset[ExitClass] = frozenset({"passed", "accepted"})
_FAILED_CLASS: ExitClass = "failed"
_BLOCKING_CLASSES: frozenset[ExitClass] = frozenset(
    {
        "failed",
        "could_not_run",
        "vacuous",
        "uninterpreted",
        "unattributed",
    }
)


def parse_accept_exits(command: str) -> frozenset[int]:
    """Return the exit integers declared acceptable at the invocation site."""
    match = _ACCEPT_EXITS_RE.search(command or "")
    if match is None:
        return frozenset()
    return frozenset(int(part.strip()) for part in match.group(1).split(",") if part.strip())


def is_ruff_command(command: str) -> bool:
    """True when *command* invokes ruff in invoke-position."""
    return bool(_RUFF_INVOKE_RE.search(command or ""))


def interpret_exit(row: Verification) -> ExitClass:
    """Classify one verification row's exit integer.

    Precedence:
    1. ``exit_code_register == unattributed`` — compound shape cannot name the
       process under test; refuse to treat the integer as a check result.
    2. Invocation-site ``ACCEPT_EXITS`` / ``accept-exits:`` — designed-fail
       probes that declared the observed code.
    3. Command-family tables (pytest, ruff, ``gate_d:*``).
    4. Generic: ``0`` → passed; any other integer → ``uninterpreted``.
    """
    if row.exit_code_register == "unattributed":
        return "unattributed"

    accepted = parse_accept_exits(row.command)
    if row.exit_code in accepted:
        return "accepted"

    command = row.command or ""
    if command.startswith("gate_d:"):
        if row.exit_code == 0:
            return "passed"
        if row.exit_code == 1:
            return "failed"
        return "uninterpreted"

    from services.git_integration_worker.cursor_sdk_test_observation import (
        is_pytest_command,
    )

    table: dict[int, ExitClass] | None = None
    if is_pytest_command(command):
        table = _PYTEST_EXIT
    elif is_ruff_command(command):
        table = _RUFF_EXIT

    if table is not None:
        return table.get(row.exit_code, "uninterpreted")
    if row.exit_code == 0:
        return "passed"
    return "uninterpreted"


def row_is_failed_check(row: Verification) -> bool:
    """True when the row ran and failed — not could-not-run / vacuous / opaque."""
    return interpret_exit(row) == _FAILED_CLASS


def row_blocks_all_pass(row: Verification) -> bool:
    """True when the row forbids the I2 SHIPPED short-circuit."""
    return interpret_exit(row) in _BLOCKING_CLASSES


def row_is_passing_class(row: Verification) -> bool:
    """True when the row's exit class is passed or accepted."""
    return interpret_exit(row) in _PASSING_CLASSES


__all__ = [
    "ExitClass",
    "interpret_exit",
    "is_ruff_command",
    "parse_accept_exits",
    "row_blocks_all_pass",
    "row_is_failed_check",
    "row_is_passing_class",
]
