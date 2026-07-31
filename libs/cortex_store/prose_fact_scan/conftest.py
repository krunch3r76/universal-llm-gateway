"""Shared fixtures for prose_fact_scan tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_store.prose_fact_scan.constants import TIER_A_GLOBS


def seed_tier_a_tree(base: Path, count: int = 127) -> None:
    """Create ``count`` Tier-A files spread across the 8 globs."""
    patterns = [
        ("notes/system/shared", "operational-context-{:03d}.md", 8),
        ("notes/system/handoffs", "handoff-{:03d}.md", 100),
        ("notes/system/session-handoff/nested", "sh-{:03d}.md", 6),
        ("notes/system/session-handoffs", "legacy-{:03d}.md", 1),
        ("notes/system/kickoffs", "kick-{:03d}.md", 1),
        ("notes/system/boot", "boot-{:03d}.md", 1),
        ("notes/system/context", "ctx-{:03d}.md", 2),
        ("notes/system/contexts", "ctxs-{:03d}.md", 8),
    ]
    created = 0
    for directory, template, quota in patterns:
        dir_path = base / directory
        dir_path.mkdir(parents=True, exist_ok=True)
        for i in range(quota):
            path = dir_path / template.format(i)
            path.write_text(f"# tier-a {created}\n", encoding="utf-8")
            created += 1
    assert created == count
    assert len(TIER_A_GLOBS) == 8


@pytest.fixture()
def scan_base(tmp_path: Path) -> Path:
    seed_tier_a_tree(tmp_path)
    return tmp_path
