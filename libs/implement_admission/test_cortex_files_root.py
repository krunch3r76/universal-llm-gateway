"""cortex_files_root preference order (friction a24515)."""

from __future__ import annotations

from pathlib import Path

import pytest

import implement_admission.closeout_helpers as closeout_helpers
from implement_admission.closeout_helpers import cortex_files_root


@pytest.mark.offline
def test_cortex_files_root_env_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    assert cortex_files_root() == tmp_path.resolve()


@pytest.mark.offline
def test_cortex_files_root_prefers_container_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MCP /data/files mount beats ~/mcp-data/files when env unset."""
    monkeypatch.delenv("CORTEX_FILES_ROOT", raising=False)
    data_files = tmp_path / "data-files"
    data_files.mkdir()
    home = tmp_path / "home"
    (home / "mcp-data" / "files").mkdir(parents=True)
    monkeypatch.setattr(closeout_helpers, "_MCP_CONTAINER_CORTEX_ROOT", data_files)
    monkeypatch.setattr(
        closeout_helpers.Path,
        "home",
        classmethod(lambda cls: home),
    )
    assert cortex_files_root() == data_files.resolve()


@pytest.mark.offline
def test_cortex_files_root_falls_back_to_home_mcp_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CORTEX_FILES_ROOT", raising=False)
    home = tmp_path / "home"
    (home / "mcp-data" / "files").mkdir(parents=True)
    missing = tmp_path / "no-container-mount"
    monkeypatch.setattr(closeout_helpers, "_MCP_CONTAINER_CORTEX_ROOT", missing)
    monkeypatch.setattr(
        closeout_helpers.Path,
        "home",
        classmethod(lambda cls: home),
    )
    assert cortex_files_root() == (home / "mcp-data" / "files").resolve()
