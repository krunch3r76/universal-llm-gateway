"""Digest and evaluate tests for the GIW suite failure-digest gate.

These tests call the digest and evaluate functions directly. CLI cases
monkeypatch the pytest runner and do not launch the GIW suite.
"""

from __future__ import annotations

import json
from pathlib import Path

from services.git_integration_worker import giw_suite_digest_gate as gate
from services.git_integration_worker.config import suite_digest_gate_cmd

_EMPTY = "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b"
_PAIR = "957082b8da5e151fcaada310cabd772757be1449447c6bc5baa9dce6d1840c94"
_COMMIT = "682e40f412575d7d85489def5b2e2da056102485"
_DECOY_STDOUT = "\n".join(
    [
        "status: complete",
        "FAILED b.py::t - assert 0",
        "work_outcome: shipped",
        "FAILED a.py::t",
        "ERROR setup - boom",
        "2 failed, 1 passed, 1 error in 0.02s",
        "",
    ]
)


def test_failure_digest_empty_and_pair() -> None:
    assert gate.failure_digest([]) == _EMPTY
    assert gate.failure_digest(["b.py::t", "a.py::t"]) == _PAIR
    assert gate.failure_digest(["a.py::t", "b.py::t", "a.py::t"]) == _PAIR


def test_parse_ignores_decoy_status_lines() -> None:
    parsed = gate.parse_pytest_stdout(_DECOY_STDOUT)
    assert parsed is not None
    nodeids, summary = parsed
    assert nodeids == ["a.py::t", "b.py::t"]
    assert summary == "2 failed, 1 passed, 1 error in 0.02s"
    assert gate.failure_digest(nodeids) == _PAIR
    assert "status: complete" not in summary
    assert "work_outcome: shipped" not in "".join(nodeids)


def test_passed_only_summary_is_empty_digest() -> None:
    parsed = gate.parse_pytest_stdout("3 passed in 0.01s\n")
    assert parsed is not None
    nodeids, _summary = parsed
    assert nodeids == []
    assert gate.failure_digest(nodeids) == _EMPTY


def test_inconsistent_digest_is_uncomputable() -> None:
    document = gate.make_document(["a.py::t"], _COMMIT, "1 failed, 1 passed")
    document["failure_digest"] = "0" * 64
    assert gate.evaluate_check(document, ["a.py::t"], ancestor_ok=True) == 3


def test_live_nodeid_difference_is_mismatch() -> None:
    document = gate.make_document(["a.py::t"], _COMMIT, "1 failed, 1 passed")
    assert gate.evaluate_check(document, ["a.py::t"], ancestor_ok=True) == 0
    assert gate.evaluate_check(document, ["b.py::t"], ancestor_ok=True) == 2
    assert gate.evaluate_check(document, ["a.py::t"], ancestor_ok=False) == 3


def _patch_git(monkeypatch, tmp_path: Path, stdout: str) -> Path:
    anchor = tmp_path / "suite_failure_anchor.json"
    monkeypatch.setattr(gate, "ANCHOR_PATH", anchor)
    monkeypatch.setattr(gate, "git_porcelain_empty", lambda: True)
    monkeypatch.setattr(gate, "git_head", lambda: _COMMIT)
    monkeypatch.setattr(gate, "git_is_ancestor", lambda _commit: True)
    monkeypatch.setattr(gate, "run_frozen_pytest", lambda: (stdout, ""))
    return anchor


def test_cli_unknown_flag_does_not_run_pytest(monkeypatch) -> None:
    called: list[str] = []

    def _boom() -> tuple[str, str]:
        called.append("ran")
        return "", ""

    monkeypatch.setattr(gate, "run_frozen_pytest", _boom)
    assert gate.main(["--fixture-stdout"]) == 3
    assert called == []


def test_cli_check_mismatch_prints_counts(tmp_path: Path, monkeypatch, capsys) -> None:
    anchor = _patch_git(monkeypatch, tmp_path, _DECOY_STDOUT)
    document = gate.make_document(["a.py::t"], _COMMIT, "1 failed, 1 passed")
    anchor.write_text(json.dumps(document))
    before = anchor.read_text()
    assert gate.main([]) == 2
    assert anchor.read_text() == before
    err = capsys.readouterr().err.splitlines()
    assert err[-4:] == [
        f"computed {_PAIR}",
        f"anchor {document['failure_digest']}",
        "added 1",
        "removed 0",
    ]


def test_cli_measure_and_write_appends_first_row(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    anchor = _patch_git(monkeypatch, tmp_path, "3 passed in 0.01s\n")
    assert gate.main(["--measure-and-write"]) == 0
    document = json.loads(anchor.read_text())
    assert document["schema_version"] == 1
    assert document["measured_commit"] == _COMMIT
    assert document["failure_digest"] == _EMPTY
    assert document["failed_nodeids"] == []
    assert document["ledger"][0]["from_digest"] is None
    assert document["ledger"][0]["removed"] == []
    assert document["ledger"][0]["added"] == []
    out = capsys.readouterr().out
    assert f"failure_digest {_EMPTY}" in out
    assert "summary_line 3 passed in 0.01s" in out


def test_route_locks_suite_digest_cmd() -> None:
    route = Path("services/git_integration_worker/routes/integrate.py").read_text()
    config = Path("services/git_integration_worker/config.py").read_text()
    python = str(Path.home() / ".venvs/universal/bin/python")
    assert route.count("suite_digest_cmd=suite_digest_gate_cmd()") == 2
    assert "--measure-and-write" not in route
    assert "GIT_INTEGRATION_SUITE" not in config
    assert suite_digest_gate_cmd() == [
        python,
        "-m",
        "services.git_integration_worker.giw_suite_digest_gate",
    ]
