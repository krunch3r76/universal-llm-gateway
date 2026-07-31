"""Unit tests for cortex SOT skill-reference mining."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_CORTEX = Path(__file__).resolve().parents[2] / "scripts" / "cortex"
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from _skill_sot_miner import mine_all_sot_edges, mine_sot_file


@pytest.fixture()
def sot_tree(tmp_path: Path) -> tuple[Path, Path]:
    skills = tmp_path / "agent-skills"
    skills.mkdir()
    ws = tmp_path / "projects"
    (ws / "universal-llm-gateway" / ".cursor" / "skills" / "friction-review").mkdir(
        parents=True
    )
    (
        ws
        / "universal-llm-gateway"
        / ".cursor"
        / "skills"
        / "friction-review"
        / "SKILL.md"
    ).write_text("# friction\n", encoding="utf-8")
    (skills / "architecture-invariants.md").write_text("# inv\n", encoding="utf-8")
    (skills / "consult-routing.md").write_text(
        """---
related_skills: ["friction-review"]
---
Load `.cursor/skills/friction-review/SKILL.md` and
`cortex://agent-skills/architecture-invariants.md` before dispatch.
""",
        encoding="utf-8",
    )
    return skills, ws


def test_mine_sot_file_frontmatter_and_patterns(sot_tree: tuple[Path, Path]) -> None:
    skills, ws = sot_tree
    targets = mine_sot_file(skills / "consult-routing.md", sot_root=skills, ws_root=ws)
    assert targets == {"architecture-invariants", "friction-review"}


def test_mine_all_sot_edges_skips_readme(tmp_path: Path) -> None:
    skills = tmp_path / "agent-skills"
    skills.mkdir()
    (skills / "README.md").write_text("index\n", encoding="utf-8")
    (skills / "foo.md").write_text(
        "see cortex://agent-skills/bar.md\n", encoding="utf-8"
    )
    (skills / "bar.md").write_text("# bar\n", encoding="utf-8")
    mined = mine_all_sot_edges(
        sot_root=tmp_path, ws_root=tmp_path / "projects", valid_targets={"bar"}
    )
    assert mined == {"foo": {"bar"}}


def test_mine_sot_file_skips_retired_targets(sot_tree: tuple[Path, Path]) -> None:
    skills, ws = sot_tree
    (skills / "delegate-to-grok.md").write_text(
        "see cortex://agent-skills/grokbuild.md\n", encoding="utf-8"
    )
    (skills / "grokbuild.md").write_text("# retired\n", encoding="utf-8")
    targets = mine_sot_file(skills / "delegate-to-grok.md", sot_root=skills, ws_root=ws)
    assert targets == set()
