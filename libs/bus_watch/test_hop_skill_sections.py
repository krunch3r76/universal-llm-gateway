"""Hop/resume skill section load plans."""

from __future__ import annotations

import pytest

from bus_watch.hop_skill_sections import (
    LoadedKey,
    build_skill_section_plan,
    format_skill_load_block,
    measure_operation_skill_utf8,
)

pytestmark = pytest.mark.offline


def test_checkpoint_hop_plan_loads_section_bodies() -> None:
    bodies, overhead = measure_operation_skill_utf8("checkpoint_hop")
    assert bodies > 0
    assert overhead > 0


def test_stale_skip_only_when_hash_matches() -> None:
    plan = build_skill_section_plan("house_resume")
    first = next(row for row in plan if row.status == "ok")
    loaded = {_load_key(first)}
    again = build_skill_section_plan("house_resume", already_loaded=loaded)
    assert any(row.status == "stale_skip" for row in again)


def _load_key(row) -> LoadedKey:
    assert row.read_sha256
    return (row.slug, row.section, row.read_sha256)


def test_house_resume_block_lists_md_read() -> None:
    block = format_skill_load_block("house_resume")
    assert "md_read" in block
    assert "house_resume" in block


def test_compact_block_names_sections() -> None:
    plan = build_skill_section_plan("house_resume")
    block = format_skill_load_block("house_resume", detail="compact")
    assert "checkpoint-discipline" in block
    assert "..." not in block
    for row in plan:
        if row.status == "ok":
            assert row.section in block
            assert row.path in block
        if row.status == "missing_section":
            assert "BLOCKED" in block


def test_core_sections_do_not_double_count_h1() -> None:
    bodies, _overhead = measure_operation_skill_utf8("house_resume")
    assert bodies < 25_000
