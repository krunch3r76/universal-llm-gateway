"""House-rules byte gate on markdown section write paths (friction 39338 r5)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tools import markdown_tool
from tools.filesystem import _ops_text as ops_text
from tools.filesystem import _ops_write as ops_write
from tools.filesystem import _paths as paths
from tools.filesystem import _write_authority as authority


@pytest.fixture
def sandbox_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(paths, "SANDBOX_ROOT", root)
    monkeypatch.setattr(markdown_tool, "_FILES_ROOT", root)
    return root


def _house_rules_over_budget(root: Path, rel: str, size: int = 8817) -> None:
    intro = "## HOUSE RULES\n"
    body = intro + "x" * (size - len(intro.encode("utf-8")))
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _assert_rotation_refusal(rejected: dict) -> None:
    assert rejected.get("reason") == "house_rules.over_budget"
    assert "rotation" in rejected["error"]
    assert "12286-rulings-archive.md" in rejected["error"]


def test_md_replace_house_rules_growth_refused(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    _house_rules_over_budget(sandbox_root, rel)
    before = (sandbox_root / rel).read_bytes()
    rejected = markdown_tool._section_patch_result(
        sandbox_root / rel,
        rel,
        "cortex",
        "HOUSE RULES",
        "x",
        "xx",
    )
    _assert_rotation_refusal(rejected)
    assert (sandbox_root / rel).read_bytes() == before


def test_md_append_house_rules_growth_refused(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    _house_rules_over_budget(sandbox_root, rel)
    before = (sandbox_root / rel).read_bytes()
    rejected = markdown_tool._section_write_result(
        sandbox_root / rel,
        rel,
        "cortex",
        "HOUSE RULES",
        "mcp.tool.markdown.section.appended",
        "appended",
        lambda t: t + "Z" * 64,
        mutation_op="md_append",
    )
    _assert_rotation_refusal(rejected)
    assert (sandbox_root / rel).read_bytes() == before


def test_md_rewrite_section_house_rules_growth_refused(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    _house_rules_over_budget(sandbox_root, rel)
    before = (sandbox_root / rel).read_bytes()
    rejected = markdown_tool._section_write_result(
        sandbox_root / rel,
        rel,
        "cortex",
        "HOUSE RULES",
        "mcp.tool.markdown.section.rewritten",
        "rewritten",
        lambda t: t + ("y" * 64),
        mutation_op="md_rewrite_section",
    )
    _assert_rotation_refusal(rejected)
    assert (sandbox_root / rel).read_bytes() == before


def test_md_insert_house_rules_growth_refused(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    _house_rules_over_budget(sandbox_root, rel)
    before = (sandbox_root / rel).read_bytes()
    rejected = markdown_tool._section_write_result(
        sandbox_root / rel,
        rel,
        "cortex",
        "HOUSE RULES",
        "mcp.tool.markdown.section.inserted",
        "inserted",
        lambda t: t + "\n- K99 extra " + ("z" * 64),
        mutation_op="md_insert",
    )
    _assert_rotation_refusal(rejected)
    assert (sandbox_root / rel).read_bytes() == before


def test_md_delete_house_rules_size_gate_binds(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    body = "## HOUSE RULES\n" + ("x" * 8480) + "\n## Annex\n" + ("y" * 400) + "\n"
    path = sandbox_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    before = path.read_bytes()
    landed = markdown_tool._section_delete_result(
        path,
        rel,
        "cortex",
        "Annex",
    )
    assert "error" not in landed
    assert len(path.read_bytes()) < len(before)
    assert len(path.read_bytes()) > authority.HOUSE_RULES_MAX_BYTES
    path.write_text(body, encoding="utf-8")
    with patch.object(
        markdown_tool,
        "evaluate_house_rules_post_edit_size",
        return_value={
            "error": "Use rotation — 12286-rulings-archive.md",
            "reason": "house_rules.over_budget",
        },
    ):
        rejected = markdown_tool._section_delete_result(
            path,
            rel,
            "cortex",
            "Annex",
        )
    _assert_rotation_refusal(rejected)
    assert path.read_bytes() == before
