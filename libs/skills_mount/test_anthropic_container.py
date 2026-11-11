"""Anthropic container.skills partition (skills-delivery ruling leg B)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skills_mount.anthropic_container import (
    clear_anthropic_skills_index_cache_for_tests,
    partition_slugs_for_container,
)


@pytest.fixture(autouse=True)
def _clear_index_cache() -> None:
    clear_anthropic_skills_index_cache_for_tests()
    yield
    clear_anthropic_skills_index_cache_for_tests()


def test_unindexed_caller_overflows_to_inline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({"skills": {}}), encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_SKILLS_INDEX_PATH", str(index_path))
    clear_anthropic_skills_index_cache_for_tests()

    mount, inline = partition_slugs_for_container(
        (("custom-caller-skill", "caller"),),
    )
    assert mount == ()
    assert inline == ("custom-caller-skill",)


def test_nine_indexed_mounts_eight_rest_inline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skills = {
        f"skill-{i}": {"skill_id": f"sk_{i}", "type": "custom", "version": "latest"}
        for i in range(9)
    }
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({"skills": skills}), encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_SKILLS_INDEX_PATH", str(index_path))
    clear_anthropic_skills_index_cache_for_tests()

    ordered = tuple((f"skill-{i}", "caller") for i in range(9))
    mount, inline = partition_slugs_for_container(ordered)
    assert mount == tuple(f"skill-{i}" for i in range(8))
    assert inline == ("skill-8",)
