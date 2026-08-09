from __future__ import annotations

from pathlib import Path

import pytest

from tools.filesystem import _cross_sandbox as cross_sandbox


def _sandbox_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path]:
    cortex_root = tmp_path / "files"
    workspaces_root = tmp_path / "project"
    cortex_root.mkdir()
    workspaces_root.mkdir()
    monkeypatch.setenv("PROJECT_ROOT", str(workspaces_root))
    monkeypatch.delenv("LIFE_PROJECT_ROOT", raising=False)
    monkeypatch.setattr(cross_sandbox, "SANDBOX_ROOT", cortex_root)
    monkeypatch.setattr(cross_sandbox, "record", lambda *_args, **_kwargs: None)
    return cortex_root, workspaces_root


def test_copy_between_sandboxes_workspaces_to_cortex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cortex_root, workspaces_root = _sandbox_roots(monkeypatch, tmp_path)
    source = workspaces_root / "universal-llm-gateway" / "case.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF-1.7\nfixture")

    result = cross_sandbox.copy_between_sandboxes_impl(
        "workspaces",
        "universal-llm-gateway/case.pdf",
        "cortex",
        "notes/legal/case.pdf",
    )

    destination = cortex_root / "notes" / "legal" / "case.pdf"
    assert destination.read_bytes() == b"%PDF-1.7\nfixture"
    assert result["status"] == "copied"
    assert result["source_sandbox"] == "workspaces"
    assert result["target_sandbox"] == "cortex"


def test_copy_between_sandboxes_cortex_to_workspaces(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cortex_root, workspaces_root = _sandbox_roots(monkeypatch, tmp_path)
    source = cortex_root / "notes" / "legal" / "case.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"case bytes")

    cross_sandbox.copy_between_sandboxes_impl(
        "cortex",
        "notes/legal/case.pdf",
        "workspaces",
        "universal-llm-gateway/tmp/case.pdf",
    )

    destination = workspaces_root / "universal-llm-gateway" / "tmp" / "case.pdf"
    assert destination.read_bytes() == b"case bytes"


def test_copy_between_sandboxes_rejects_traversal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _sandbox_roots(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="traversal"):
        cross_sandbox.copy_between_sandboxes_impl(
            "workspaces",
            "../outside.pdf",
            "cortex",
            "notes/outside.pdf",
        )


def test_life_copy_to_workspaces_uses_life_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    life_root = tmp_path / "life"
    cortex_root = tmp_path / "files"
    shared.mkdir()
    life_root.mkdir()
    cortex_root.mkdir()
    (life_root / "universal-llm-gateway").mkdir(parents=True)
    source = cortex_root / "notes" / "probe.txt"
    source.parent.mkdir(parents=True)
    source.write_text("payload")

    monkeypatch.setenv("PROJECT_ROOT", str(shared))
    monkeypatch.setenv("LIFE_PROJECT_ROOT", str(life_root))
    monkeypatch.setattr(cross_sandbox, "SANDBOX_ROOT", cortex_root)
    monkeypatch.setattr(cross_sandbox, "record", lambda *_args, **_kwargs: None)

    cross_sandbox.copy_between_sandboxes_impl(
        "cortex",
        "notes/probe.txt",
        "workspaces",
        "universal-llm-gateway/tmp/probe.txt",
        surface="life",
    )

    life_target = life_root / "universal-llm-gateway" / "tmp" / "probe.txt"
    shared_target = shared / "universal-llm-gateway" / "tmp" / "probe.txt"
    assert life_target.read_text() == "payload"
    assert not shared_target.exists()
