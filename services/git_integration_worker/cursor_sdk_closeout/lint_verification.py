"""Closeout-time ruff measurement: touched-files lint and GIW-subtree F821.

Owns toolchain identity (PATH binary + ``ruff version``), retained-stream
truncation, ``run_touched_files_lint`` (cwd pinned to ``source_repo``), and
``run_giw_subtree_f821_lint`` (delegates to ``giw_f821_gate`` via a
function-local import). ``_LINT_OUTPUT_RETAIN_CHARS`` and
``_ruff_toolchain_identity`` stay private to this module; tests that imported
them from the package must be repointed here. ``subprocess.run`` on this
module is the patch target for touched-files lint tests.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from implement_admission.closeout_models import (
    Verification,
    derived_gate_verification,
    observed_process_verification,
)

from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet

# Cap retained lint streams so a noisy ruff failure cannot inflate closeout JSON.
# Marker suffix records the cut when either stream exceeds the budget.
_LINT_OUTPUT_RETAIN_CHARS = 4000


def _decode_retained_stream(raw: bytes | str | None) -> tuple[str, bool]:
    """Decode a subprocess stream and truncate to ``_LINT_OUTPUT_RETAIN_CHARS``.

    Returns ``(text, truncated)``. Empty/absent streams become ``""``.
    """
    if raw is None:
        return "", False
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    if len(text) <= _LINT_OUTPUT_RETAIN_CHARS:
        return text, False
    return (
        text[:_LINT_OUTPUT_RETAIN_CHARS] + "\n...[truncated]",
        True,
    )


def _ruff_toolchain_identity() -> tuple[str, str]:
    """Return ``(executable, version)`` for the ``ruff`` PATH will run.

    Version is probed from that binary (``ruff version``), not
    ``importlib.metadata`` — a venv-installed wheel next to a shadowed
    ``~/.local/bin/ruff`` is exactly the mismatch this stamp exists to name.
    """
    executable = shutil.which("ruff") or "ruff"
    try:
        probe = subprocess.run(
            [executable, "version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return executable, "unknown"
    text = (probe.stdout or probe.stderr or "").strip()
    if not text:
        return executable, "unknown"
    parts = text.split()
    return executable, parts[-1]


def run_touched_files_lint(
    source_repo: Path,
    change_set: ChangeSet,
) -> tuple[Verification, str | None]:
    """Run ``ruff check`` on touched ``*.py`` paths from the git change set.

    Each call mints a fresh ``invocation_id`` so this closeout-time process
    cannot be silently conflated with a mid-run agent shell that happened to
    print ``All checks passed!`` (specimen auto-00a23d2a4f45). ``cwd`` is
    pinned to ``source_repo`` so config discovery matches in-tree measurement.

    On non-zero exit, stdout/stderr are retained on the verification row
    (each truncated at ``_LINT_OUTPUT_RETAIN_CHARS``) so a later
    ``checks_failed`` grade remains interrogable.

    The verification row carries ``executable`` / ``tool_version`` for the
    binary PATH resolved (arc 7190) so ``work_outcome`` is falsifiable
    against a known toolchain, not an implicit GIW PATH.
    """
    py_paths = [
        path
        for path in (*change_set.created, *change_set.modified)
        if path.endswith(".py")
    ]
    if not py_paths:
        return (
            derived_gate_verification(
                command="ruff check (no python files touched)",
                exit_code=0,
                basis="lint_skipped_no_python",
                invocation_id=f"lint-skip:{uuid4().hex}",
            ),
            None,
        )
    abs_paths = [str(source_repo / path) for path in py_paths]
    command = f"ruff check {len(py_paths)} touched files"
    invocation_id = f"lint:{uuid4().hex}"
    executable, tool_version = _ruff_toolchain_identity()
    try:
        # Pin cwd to the owning repo root so isort/first-party discovery matches
        # in-tree measurement (orphan cwd → phantom I001 on otherwise-clean files).
        proc = subprocess.run(
            ["ruff", "check", *abs_paths],
            capture_output=True,
            timeout=60,
            cwd=str(source_repo),
        )
    except FileNotFoundError:
        return (
            derived_gate_verification(
                command=command,
                exit_code=0,
                basis="lint_unavailable_ruff_missing",
                invocation_id=invocation_id,
            ),
            "verification:lint_unavailable",
        )
    except subprocess.TimeoutExpired:
        return (
            derived_gate_verification(
                command=command,
                exit_code=0,
                basis="lint_unavailable_timeout",
                invocation_id=invocation_id,
            ),
            "verification:lint_unavailable",
        )
    stdout: str | None = None
    stderr: str | None = None
    output_truncated = False
    if proc.returncode != 0:
        stdout, trunc_out = _decode_retained_stream(proc.stdout)
        stderr, trunc_err = _decode_retained_stream(proc.stderr)
        output_truncated = trunc_out or trunc_err
    return (
        observed_process_verification(
            command=command,
            exit_code=proc.returncode,
            invocation_id=invocation_id,
            basis="subprocess.run.returncode",
            stdout=stdout,
            stderr=stderr,
            output_truncated=output_truncated,
            executable=executable,
            tool_version=tool_version,
        ),
        None,
    )


def run_giw_subtree_f821_lint(
    source_repo: Path,
) -> tuple[Verification, str | None]:
    """Run ``ruff check --select F821`` on the GIW package subtree.

    Whole-repo ruff is blocked by pre-existing master lint debt; this
    F821-only pass on ``services/git_integration_worker/`` closes the
    enforcement gap where undefined-name defects in the dispatch substrate
    landed despite F821 being enabled project-wide (arc 6655).

    Grading-only at closeout — blocking enforcement lives in
    :func:`salvage_commit` and the ``git_land`` green gate (before land).
    """
    from services.git_integration_worker.giw_f821_gate import run_giw_subtree_f821_check

    invocation_id = f"lint-giw-f821:{uuid4().hex}"
    result = run_giw_subtree_f821_check(source_repo)
    if result.stderr.strip() == "ruff missing — gate skipped":
        return (
            derived_gate_verification(
                command=result.command,
                exit_code=0,
                basis="lint_unavailable_ruff_missing",
                invocation_id=invocation_id,
            ),
            "verification:lint_unavailable",
        )
    if result.exit_code == 124:
        return (
            derived_gate_verification(
                command=result.command,
                exit_code=0,
                basis="lint_unavailable_timeout",
                invocation_id=invocation_id,
            ),
            "verification:lint_unavailable",
        )
    stdout: str | None = None
    stderr: str | None = None
    output_truncated = False
    if result.exit_code != 0:
        stdout, trunc_out = _decode_retained_stream(result.stdout)
        stderr, trunc_err = _decode_retained_stream(result.stderr)
        output_truncated = trunc_out or trunc_err
    return (
        observed_process_verification(
            command=result.command,
            exit_code=result.exit_code,
            invocation_id=invocation_id,
            basis="subprocess.run.returncode",
            stdout=stdout,
            stderr=stderr,
            output_truncated=output_truncated,
        ),
        None,
    )
