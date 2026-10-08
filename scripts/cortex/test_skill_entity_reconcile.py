"""Catalog entity reconcile fails closed on source_uri drift."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from _skill_audit import _file_gone_ids  # noqa: E402
from _skill_constants import _WS  # noqa: E402
from _skill_entity_reconcile import (  # noqa: E402
    _source_uri_failures,
    run_entity_reconcile_check,
)

_PLUGIN = f"{_WS}/cursor-plugins/ulg-ecosystem/skills/abstraction-layering/SKILL.md"


class _Resp:
    def __init__(self, status: int, payload: dict) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _Client:
    def __init__(self, rows: dict[str, dict]) -> None:
        self._rows = rows

    def request(self, method: str, path: str, **kwargs: object) -> _Resp:
        del method, kwargs
        if path.startswith("/entities?"):
            items = [
                {"id": f"agent_skill:{slug}", **row} for slug, row in self._rows.items()
            ]
            return _Resp(200, {"items": items})
        if path.startswith("/entities/agent_skill:"):
            slug = path.split("agent_skill:", 1)[1].split("?", 1)[0]
            row = self._rows.get(slug)
            if row is None:
                return _Resp(404, {})
            return _Resp(200, {"id": f"agent_skill:{slug}", **row})
        return _Resp(500, {})


def test_reconcile_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skill = (
        tmp_path
        / "cursor-plugins"
        / "ulg-ecosystem"
        / "skills"
        / "abstraction-layering"
    )
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    uri = f"{_WS}/" + (skill / "SKILL.md").relative_to(tmp_path).as_posix()
    monkeypatch.setattr(
        "implement_admission.skill_catalog_resolver.resolve_canonical_source_uri",
        lambda slug: uri,
    )
    failures = _source_uri_failures(
        {"abstraction-layering": {"source_uri": uri}},
        ["abstraction-layering"],
        repo_root=tmp_path,
    )
    assert failures == []


def test_missing_entity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "implement_admission.skill_catalog_resolver.resolve_canonical_source_uri",
        lambda slug: _PLUGIN,
    )
    failures = _source_uri_failures({}, ["abstraction-layering"], repo_root=tmp_path)
    assert failures == ["missing entity: abstraction-layering"]


def test_null_source_uri(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "implement_admission.skill_catalog_resolver.resolve_canonical_source_uri",
        lambda slug: _PLUGIN,
    )
    failures = _source_uri_failures(
        {"abstraction-layering": {"source_uri": None}},
        ["abstraction-layering"],
        repo_root=tmp_path,
    )
    assert failures == ["null source_uri: abstraction-layering"]


def test_source_uri_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "implement_admission.skill_catalog_resolver.resolve_canonical_source_uri",
        lambda slug: _PLUGIN,
    )
    failures = _source_uri_failures(
        {
            "abstraction-layering": {
                "source_uri": f"{_WS}/.cursor/skills/abstraction-layering/SKILL.md"
            }
        },
        ["abstraction-layering"],
        repo_root=tmp_path,
    )
    assert len(failures) == 1
    assert failures[0].startswith("source_uri mismatch:")


def test_resolved_file_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    uri = f"{_WS}/cursor-plugins/ulg-ecosystem/skills/abstraction-layering/SKILL.md"
    monkeypatch.setattr(
        "implement_admission.skill_catalog_resolver.resolve_canonical_source_uri",
        lambda slug: uri,
    )
    failures = _source_uri_failures(
        {"abstraction-layering": {"source_uri": uri}},
        ["abstraction-layering"],
        repo_root=tmp_path,
    )
    assert len(failures) == 1
    assert failures[0].startswith("resolved file missing:")


def test_run_check_fails_on_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "implement_admission.skill_catalog_resolver.resolve_canonical_source_uri",
        lambda slug: _PLUGIN,
    )
    monkeypatch.setattr(
        "_skill_entity_reconcile._catalog_slugs",
        lambda indexed: ["abstraction-layering"],
    )
    assert run_entity_reconcile_check(client=_Client({}), repo_root=tmp_path) == 1


def test_file_gone_covers_plugin_and_claude_prefixes(tmp_path: Path) -> None:
    live = {
        "agent_skill:plugin-gone": {
            "lifecycle": "active",
            "source_uri": f"{_WS}/cursor-plugins/ulg-ecosystem/skills/plugin-gone/SKILL.md",
        },
        "agent_skill:life-gone": {
            "lifecycle": "active",
            "source_uri": f"{_WS}/.claude/skills/life-gone/SKILL.md",
        },
        "agent_skill:cursor-present": {
            "lifecycle": "active",
            "source_uri": f"{_WS}/.cursor/skills/cursor-present/SKILL.md",
        },
    }
    present = tmp_path / ".cursor" / "skills" / "cursor-present"
    present.mkdir(parents=True)
    (present / "SKILL.md").write_text("ok", encoding="utf-8")
    gone = _file_gone_ids(live, set(), tmp_path)
    assert "agent_skill:plugin-gone" in gone
    assert "agent_skill:life-gone" in gone
    assert "agent_skill:cursor-present" not in gone
