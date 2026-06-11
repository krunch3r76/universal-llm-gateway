from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import quality


def test_resolves_repo_relative_path(tmp_path: Path) -> None:
    repo = tmp_path / "universal-llm-gateway"
    target = repo / "services" / "universal-stargate" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text("pass\n")

    quality._PROJECT_ROOT = repo

    assert quality._resolve_existing_files(
        ["services/universal-stargate/example.py"]
    ) == [str(target)]


def test_resolves_repo_prefixed_path_for_single_repo_root(tmp_path: Path) -> None:
    repo = tmp_path / "universal-llm-gateway"
    target = repo / "services" / "universal-stargate" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text("pass\n")

    quality._PROJECT_ROOT = repo

    assert quality._resolve_existing_files(
        ["universal-llm-gateway/services/universal-stargate/example.py"]
    ) == [str(target)]


def test_resolves_repo_relative_path_for_multi_repo_root(tmp_path: Path) -> None:
    repo = tmp_path / "universal-llm-gateway"
    (repo / ".git").mkdir(parents=True)
    target = repo / "services" / "universal-stargate" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text("pass\n")

    quality._PROJECT_ROOT = tmp_path

    assert quality._resolve_existing_files(
        ["services/universal-stargate/example.py"]
    ) == [str(target)]


def test_resolves_repo_relative_path_when_git_metadata_is_unmounted(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "universal-llm-gateway"
    target = repo / "services" / "universal-stargate" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text("pass\n")

    quality._PROJECT_ROOT = tmp_path

    assert quality._resolve_existing_files(
        ["services/universal-stargate/example.py"]
    ) == [str(target)]


def test_resolves_host_absolute_path_by_repo_name(tmp_path: Path) -> None:
    repo = tmp_path / "universal-llm-gateway"
    target = repo / "services" / "universal-stargate" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text("pass\n")

    quality._PROJECT_ROOT = repo

    assert quality._resolve_existing_files(
        [
            "/mnt/torus/projects/universal-llm-gateway/services/universal-stargate/example.py"
        ]
    ) == [str(target)]


def test_run_ruff_uses_python_module(monkeypatch: Any) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(quality.subprocess, "run", fake_run)

    result = quality._run_ruff(["example.py"])

    assert result == {"passed": True, "output": "ok\n"}
    assert commands == [[sys.executable, "-m", "ruff", "check", "example.py"]]


def test_run_offline_tests_uses_marker_selection(monkeypatch: Any) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="74 passed\n", stderr="")

    monkeypatch.setattr(quality.subprocess, "run", fake_run)

    result = quality._run_offline_tests(
        ["/data/project/libs/llm_adapters/test_dispatch_registry_coherence.py"]
    )

    assert result == {"passed": True, "output": "74 passed"}
    assert commands == [
        [sys.executable, "-c", "import pytest"],
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "offline",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            "libs/llm_adapters",
            "libs/model_id",
        ],
    ]


def test_run_offline_tests_skips_without_closure_path(monkeypatch: Any) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(quality.subprocess, "run", fake_run)

    result = quality._run_offline_tests(["/data/project/services/mcp-server/server.py"])

    assert result == {
        "passed": True,
        "output": "no offline-closure files touched; skipped",
    }
    assert commands == []


def test_run_offline_tests_fail_closed_when_pytest_absent(monkeypatch: Any) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="ModuleNotFoundError: No module named 'pytest'",
        )

    monkeypatch.setattr(quality.subprocess, "run", fake_run)

    result = quality._run_offline_tests(
        ["/data/project/libs/llm_adapters/test_max_output_parity.py"]
    )

    assert result["passed"] is False
    assert "pytest unavailable" in str(result["output"])
    assert len(commands) == 1
    assert commands[0] == [sys.executable, "-c", "import pytest"]


@pytest.mark.parametrize(
    ("returncode", "output", "expected"),
    [
        (0, "anything", "passed"),
        (
            1,
            "check-imports: path outside stargate/libs trees: /x/quality.py",
            "skipped",
        ),
        (1, "check-imports: no Python files to check", "skipped"),
        (1, "check-imports: FAILED systems.x: ImportError: ...", "failed"),
        (1, "path outside stargate/libs trees ... FAILED mod: e", "failed"),
        (1, "some unrecognised stderr", "failed"),
    ],
)
def test_classify_import_check(returncode: int, output: str, expected: str) -> None:
    assert quality._classify_import_check(returncode, output) == expected


def test_run_import_check_skips_out_of_scope_path(
    monkeypatch: Any, tmp_path: Path
) -> None:
    repo = tmp_path / "universal-llm-gateway"
    check_script = repo / "scripts" / "check-imports"
    check_script.parent.mkdir(parents=True)
    check_script.write_text("#!/usr/bin/env python3\n")
    target = repo / "services" / "mcp-server" / "tools" / "quality.py"
    target.parent.mkdir(parents=True)
    target.write_text("pass\n")

    quality._PROJECT_ROOT = repo

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="check-imports: path outside stargate/libs trees: /x/quality.py",
        )

    monkeypatch.setattr(quality.subprocess, "run", fake_run)

    result = quality._run_import_check([str(target)])

    assert result["passed"] is True
    assert result.get("skipped") is True


def test_run_offline_tests_implement_admission_suite(monkeypatch: Any) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="42 passed\n", stderr="")

    monkeypatch.setattr(quality.subprocess, "run", fake_run)

    result = quality._run_offline_tests(
        ["/data/project/libs/implement_admission/materialize.py"]
    )

    assert result == {"passed": True, "output": "42 passed"}
    assert len(commands) == 2
    assert commands[0] == [sys.executable, "-c", "import pytest"]
    pytest_cmd = commands[1]
    assert pytest_cmd[:3] == [sys.executable, "-m", "pytest"]
    assert pytest_cmd[3:5] == ["--import-mode", "importlib"]
    assert "-m" not in pytest_cmd[5:]
    assert (
        "services/universal-stargate/systems/frontier_consult/test_team_handoff.py"
        in pytest_cmd
    )
    assert (
        len([p for p in pytest_cmd if p.startswith("services/universal-stargate")]) == 6
    )
