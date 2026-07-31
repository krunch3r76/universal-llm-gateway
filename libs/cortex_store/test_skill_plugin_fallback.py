"""Plugin SOT fallback for skills whose table URI misses on disk."""

from __future__ import annotations

from pathlib import Path

from cortex_store.routes.boot._skill_trigger import _resolve_skill_file


def test_resolve_skill_file_falls_back_to_plugin_sot(monkeypatch) -> None:
    monkeypatch.setattr(
        "cortex_store.routes.boot._skill_trigger._WORKSPACES_ROOT",
        Path("/mnt/torus/projects"),
    )
    monkeypatch.setattr(
        "cortex_store.routes.boot._skill_trigger._FILES_ROOT",
        __import__("pathlib").Path("/nonexistent/files"),
    )
    path = _resolve_skill_file(
        "workspaces://universal-llm-gateway/.cursor/skills/path-sim/SKILL.md",
        "path-sim",
    )
    assert path is not None
    assert "cursor-plugins/ulg-ecosystem/skills/path-sim/SKILL.md" in str(path)
