"""Regression tests for substrate_feedback finding extraction."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from unittest.mock import MagicMock, patch

from bus_watch.substrate_feedback import _fetch_active_claims, extract_substrate_findings

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


# Specimen a:36740 / agent-bus:13132 — intentional RED half, then GREEN.
_FALSIFIER_NODE_A = (
    "services/cdp_ask/tests/test_liveness.py"
    "::test_timeout_derived_unhealthy_does_not_nudge_sync_restart"
)
_FALSIFIER_NODE_B = (
    "services/cdp_ask/tests/test_liveness.py"
    "::test_unknown_health_does_not_nudge_sync_restart"
)
_RED_THEN_GREEN_CLOSEOUT = f"""
RED_PROBE_EXIT=1
FAILED {_FALSIFIER_NODE_A}
FAILED {_FALSIFIER_NODE_B}
2 failed in 0.12s
GREEN_PROBE_EXIT=0
2 passed in 0.09s
"""


def test_red_then_green_falsifier_is_not_rot() -> None:
    """A deliberate RED probe bracketed by GREEN must not mint rot findings."""
    assert extract_substrate_findings(_RED_THEN_GREEN_CLOSEOUT) == []


def test_same_node_passed_later_is_not_rot() -> None:
    """Same node id FAILED then PASSED in one episode is a falsifier, not rot."""
    text = f"FAILED {_FALSIFIER_NODE_A}\nPASSED {_FALSIFIER_NODE_A}\n"
    assert extract_substrate_findings(text) == []


def test_unbracketed_pytest_failure_is_still_rot() -> None:
    text = f"FAILED {_FALSIFIER_NODE_A}\n1 failed in 0.12s\n"
    findings = extract_substrate_findings(text)
    assert findings == [f"FAILED {_FALSIFIER_NODE_A}"]


def test_failure_after_green_bracket_is_still_rot() -> None:
    later = "services/cdp_ask/tests/test_liveness.py::test_unrelated_breakage"
    text = _RED_THEN_GREEN_CLOSEOUT + f"\nFAILED {later}\n1 failed in 0.04s\n"
    findings = extract_substrate_findings(text)
    assert findings == [f"FAILED {later}"]


def test_fetch_active_claims_sends_internal_routing_headers() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"items": []}

    with patch("substrate_graph_write.write.make_sync_client") as client_factory:
        client = client_factory.return_value.__enter__.return_value
        client.post.return_value = response
        _fetch_active_claims("todo:x")

    kwargs = client.post.call_args.kwargs
    assert kwargs["headers"]["X-ULG-Caller"] == "bus_watch"
    assert "via_adapter" not in kwargs["json"]


def test_json_red_then_green_same_command_is_not_rot() -> None:
    import json

    command = "pytest -q services/cdp_ask/tests/test_liveness.py"
    payload = {
        "schema_version": 1,
        "status": "complete",
        "verification": [
            {
                "command": command,
                "exit_code": 1,
                "exit_code_register": "observed",
            },
            {
                "command": command,
                "exit_code": 0,
                "exit_code_register": "observed",
            },
        ],
    }
    assert extract_substrate_findings(json.dumps(payload)) == []
