"""Regression tests for substrate_feedback finding extraction."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from bus_watch.substrate_feedback import extract_substrate_findings

pytestmark = pytest.mark.offline


def test_green_pytest_warnings_yields_zero_findings(tmp_path: Path) -> None:
    """Green pytest summary with warnings must not mint rot findings."""
    test_file = tmp_path / "test_ok.py"
    test_file.write_text(
        textwrap.dedent(
            """
            import warnings

            def test_warns():
                warnings.warn("benign")
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-q"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert "warning" in combined.lower()
    assert extract_substrate_findings(combined) == []
    assert extract_substrate_findings("→ 77 passed, 3 warnings in 7.26s") == []


def test_genuine_pytest_failure_yields_one_finding(tmp_path: Path) -> None:
    """A failing pytest run must surface exactly one rot finding."""
    test_file = tmp_path / "test_fail.py"
    test_file.write_text(
        textwrap.dedent(
            """
            def test_broken():
                assert False, "expected failure"
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-q"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    findings = extract_substrate_findings(proc.stdout + "\n" + proc.stderr)
    assert len(findings) == 1
    assert findings[0].startswith("FAILED ")


def test_complete_json_closeout_with_warnings_verification_is_empty() -> None:
    payload = {
        "schema_version": 1,
        "status": "complete",
        "verification": [
            {
                "command": "pytest libs/foo -q",
                "exit_code": 0,
                "exit_code_register": "observed",
            }
        ],
        "summary": "dispatch auto-abc: 77 passed, 3 warnings in 7.26s",
    }
    import json

    assert extract_substrate_findings(json.dumps(payload)) == []


def test_partial_json_closeout_yields_finding() -> None:
    import json

    payload = {"schema_version": 1, "status": "partial", "summary": "dispatch auto-x"}
    findings = extract_substrate_findings(json.dumps(payload))
    assert len(findings) == 1
    assert findings[0] == "partial"
