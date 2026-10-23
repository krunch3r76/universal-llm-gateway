"""House-rules byte gate on markdown section write paths (friction 39338 r5)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import markdown_tool
from tools.filesystem import _house_rules_rotation as house_rules_rotation
from tools.filesystem import _ops_write as ops_write
from tools.filesystem import _paths as paths
from tools.filesystem import _write_authority as authority


@pytest.fixture
def sandbox_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(paths, "SANDBOX_ROOT", root)
    monkeypatch.setattr(markdown_tool, "_FILES_ROOT", root)
    # Cortex writes resolve through cortex_files_root(), which reads this env
    # per call. Patching _FILES_ROOT alone does not redirect that branch.
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
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
    sentinel = {"error": "gate-sentinel", "reason": "house_rules.over_budget"}
    with patch.object(
        markdown_tool,
        "evaluate_house_rules_post_edit_size",
        return_value=sentinel,
    ):
        rejected = markdown_tool._section_delete_result(
            path,
            rel,
            "cortex",
            "Annex",
        )
    assert rejected.get("reason") == "house_rules.over_budget"
    assert rejected.get("error") == "gate-sentinel"
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("op", "kwargs"),
    [
        ("append_section", {"section": "HOUSE RULES", "content": "Z" * 64}),
        ("rewrite_section", {"section": "HOUSE RULES", "content": "Y" * 9000}),
        (
            "insert_section",
            {
                "heading": "Extra",
                "section": "HOUSE RULES",
                "content": "z" * 64,
                "level": 2,
                "position": "after",
            },
        ),
    ],
)
def test_markdown_dispatcher_house_rules_growth_refused(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    op: str,
    kwargs: dict,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(markdown_tool, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    _house_rules_over_budget(sandbox_root, rel)
    before = (sandbox_root / rel).read_bytes()
    captured: dict = {}

    class Capture:
        def tool(self, **_kwargs):
            def deco(fn):
                captured["markdown"] = fn
                return fn

            return deco

    markdown_tool.register_markdown_tools(Capture())
    rejected = captured["markdown"](op=op, path=rel, sandbox="cortex", **kwargs)
    assert rejected.get("reason") == "house_rules.over_budget"
    assert (sandbox_root / rel).read_bytes() == before


def _house_rules_near_cap(root: Path, rel: str, *, retract_oldest: bool = True) -> None:
    intro = "## HOUSE RULES\n"
    retract_tag = " (RETRACTED by K99)" if retract_oldest else ""
    oldest = (
        f"- K1 oldest ruling row for rotation archive{retract_tag} "
        + ("x" * (60 if retract_oldest else 85))
        + "\n"
    )
    second = "- K2 second oldest ruling row for rotation archive\n"
    filler = "x" * (8190 - len((intro + oldest + second).encode("utf-8")))
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(intro + oldest + second + filler, encoding="utf-8")


def test_house_rules_append_rotates_in_one_call(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    archive_rel = "notes/system/threads/12286-rulings-archive.md"
    _house_rules_near_cap(sandbox_root, rel)
    landed = markdown_tool._section_write_result(
        sandbox_root / rel,
        rel,
        "cortex",
        "HOUSE RULES",
        "mcp.tool.markdown.section.appended",
        "appended",
        lambda t: t + "\n- K99 new ruling row\n",
        mutation_op="md_append",
    )
    assert "error" not in landed
    assert len((sandbox_root / rel).read_bytes()) <= authority.HOUSE_RULES_MAX_BYTES
    assert landed.get("rotated_rows")
    assert "K1 oldest" in landed["rotated_rows"][0]
    archive_text = (sandbox_root / archive_rel).read_text(encoding="utf-8")
    assert "K1 oldest" in archive_text
    assert landed.get("archive_uri") == authority.RULINGS_ARCHIVE_URI
    live = (sandbox_root / rel).read_text(encoding="utf-8")
    assert "- K99 new ruling row" in live
    assert authority.RULINGS_ARCHIVE_URI in live
    assert "K99 new ruling row" not in archive_text


def test_house_rules_over_budget_refuses_when_only_unretracted_rows(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    _house_rules_near_cap(sandbox_root, rel, retract_oldest=False)
    rejected = markdown_tool._section_write_result(
        sandbox_root / rel,
        rel,
        "cortex",
        "HOUSE RULES",
        "mcp.tool.markdown.section.appended",
        "appended",
        lambda t: t + "\n- K100 another binding row\n",
        mutation_op="md_append",
    )
    assert rejected.get("reason") == "house_rules.over_budget"
    assert "unretracted" in rejected.get("error", "")


def test_house_rules_rotation_archive_failure_leaves_file_unchanged(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    _house_rules_near_cap(sandbox_root, rel)
    before = (sandbox_root / rel).read_bytes()
    with patch.object(
        house_rules_rotation,
        "durable_rmw_text",
        side_effect=OSError("archive write failed"),
    ):
        rejected = markdown_tool._section_write_result(
            sandbox_root / rel,
            rel,
            "cortex",
            "HOUSE RULES",
            "mcp.tool.markdown.section.appended",
            "appended",
            lambda t: t + "\n- K99 new ruling row\n",
            mutation_op="md_append",
        )
    assert rejected.get("reason") == "house_rules.over_budget"
    assert (sandbox_root / rel).read_bytes() == before


def test_md_read_includes_full_file_read_sha256(sandbox_root: Path) -> None:
    rel = "notes/system/threads/sample-doc.md"
    body = "## One\n\nalpha\n\n## Two\n\nbeta\n"
    path = sandbox_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()

    class Capture:
        def tool(self, **_kwargs):
            def deco(fn):
                self.fn = fn
                return fn

            return deco

    cap = Capture()
    markdown_tool.register_markdown_tools(cap)
    out = cap.fn(op="read_section", path=rel, sandbox="cortex", section="Two")
    assert out.get("read_sha256") == expected
