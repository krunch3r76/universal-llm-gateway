"""Clobber-guard tests for _skill_projection (arc 3924 D-10)."""

from __future__ import annotations

from _skill_projection import _is_already_migrated, _upsert


class _FakeClient:
    def request(self, method: str, path: str, **kwargs):  # noqa: ANN003
        raise AssertionError(f"unexpected network call: {method} {path}")


def test_is_already_migrated_for_rule_and_skill() -> None:
    # Only rule retypes are treated as already-migrated; legacy skill: folds via
    # consolidate script, agent_skill: is the live type.
    assert _is_already_migrated({"type": "rule"}) is True
    assert _is_already_migrated({"type": "skill"}) is False
    assert _is_already_migrated({"type": "agent_skill"}) is False


def test_upsert_skips_already_migrated_rule(capsys) -> None:
    live = {"type": "rule", "lifecycle": "active", "attributes": {}}
    projection = {
        "id": "agent_skill:dispatch-shape",
        "description": "x",
        "source_uri": "workspaces://universal-llm-gateway/.cursor/skills/dispatch-shape/SKILL.md",
        "attributes": {},
    }
    ok = _upsert(
        _FakeClient(),
        projection,
        dry_run=False,
        live=live,
    )
    assert ok is True
    out = capsys.readouterr().out
    assert "skipped: already-migrated (rule)" in out
