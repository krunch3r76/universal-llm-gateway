from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

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
            "libs",
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


def test_run_offline_tests_fail_soft_when_pytest_absent(monkeypatch: Any) -> None:
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

    assert result["passed"] is True
    assert "pytest unavailable" in str(result["output"])
    assert len(commands) == 1
    assert commands[0] == [sys.executable, "-c", "import pytest"]
