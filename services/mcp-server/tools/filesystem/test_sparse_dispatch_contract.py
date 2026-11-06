"""Dispatcher-level sparse read contract (corrective leg)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools._file_helpers import read_file_result, read_files_batch
from tools._hashing import sha256_hex_of_file
from tools.filesystem._fs_dispatch import OP_CONSUMES, md_section_op_doc
from tools.filesystem._ops_text import read_file_impl, read_files_batch_impl

pytestmark = pytest.mark.offline


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    return root


def _patch_read(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(
        "tools.filesystem._ops_text.read_file_result",
        lambda path, **kwargs: read_file_result(path, root=root, **kwargs),
    )
    monkeypatch.setattr(
        "tools.filesystem._ops_text.read_files_batch",
        lambda paths, **kwargs: read_files_batch(paths, root=root, **kwargs),
    )
    monkeypatch.setattr(
        "tools.filesystem._ops_text.record",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "tools.filesystem._ops_text.safe_path",
        lambda path, **_k: root / path,
    )


def test_stale_manifest_envelope_has_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    _patch_read(monkeypatch, root)
    rel = "notes/doc.md"
    target = root / rel
    target.parent.mkdir(parents=True)
    target.write_text("# Title\n\nbody\n", encoding="utf-8")
    out = read_file_impl(rel, expected_read_sha256="0" * 64)
    assert out.get("outcome") == "stale_handle" or out.get("code") == "stale_handle"
    assert out.get("read_sha256")
    assert "sections" not in out or out.get("outcome") == "stale_handle"


def test_hash_on_python_target_is_param_not_applicable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    _patch_read(monkeypatch, root)
    (root / "mod.py").write_text("x = 1\n", encoding="utf-8")
    out = read_file_impl("mod.py", expected_read_sha256="ab")
    assert out.get("code") == "param_not_applicable"
    assert "expected_read_sha256" in out["data"]["keys"]


def test_single_path_export_keeps_chunk_cursor(tmp_path: Path) -> None:
    root = _root(tmp_path)
    body = "x" * 70000
    (root / "big.md").write_text(body, encoding="utf-8")
    batch = read_files_batch(["big.md"], root=root, export=True)
    payload = batch["files"]["big.md"]
    assert isinstance(payload, dict)
    assert payload.get("truncated") is True
    assert isinstance(payload.get("chunk_cursor_next"), int)
    assert "read_sha256" in payload


def test_read_multi_manifests_keep_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    _patch_read(monkeypatch, root)
    (root / "a.md").write_text("# A\n\na\n", encoding="utf-8")
    (root / "b.md").write_text("# B\n\nb\n", encoding="utf-8")
    out = read_files_batch_impl(["a.md", "b.md"])
    assert out.get("selection") == "multi"
    assert out["files"]["a.md"]["selection"] == "manifest"
    assert "content" not in out["files"]["a.md"]


def test_share_uri_map_key_matches_relative_path(tmp_path: Path) -> None:
    root = _root(tmp_path)
    rel = "notes/doc.md"
    target = root / rel
    target.parent.mkdir(parents=True)
    target.write_text("# Title\n\nbody\n", encoding="utf-8")
    live = sha256_hex_of_file(target)
    out = read_files_batch(
        [rel],
        root=root,
        sections=["Title"],
        expected_read_sha256={f"cortex://{rel}": live},
    )
    assert out.get("code") != "param_not_applicable"
    payload = out["files"][rel]
    items = payload.get("items") or []
    assert items and items[0].get("outcome") == "ok"


def test_leaf_fallback_reports_real_children(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "nest.md").write_text(
        "# Parent\n\nonly own\n\n## Child\n\nnested heading text\n",
        encoding="utf-8",
    )
    out = read_file_result("nest.md", root=root, section="Parent")
    assert out["outcome"] == "ok"
    assert "nested heading text" not in out["content"]
    assert out["includes_children"] is False
    assert out["children"]
    assert out["char_count"] == len(out["content"])
    assert out.get("sparse_policy")


def test_preamble_char_count_matches_body(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "pre.md").write_text("front\n\n# Title\n\nbody\n", encoding="utf-8")
    out = read_file_result("pre.md", root=root, section="__preamble__")
    assert out["outcome"] == "ok"
    assert out["content"].startswith("front")
    assert out["char_count"] == len(out["content"])
    assert out["char_count"] > 0


def test_descriptions_name_child_headings() -> None:
    doc = md_section_op_doc()
    for op in ("md_replace", "md_rewrite_section", "md_delete"):
        assert op in doc
    assert doc.count("includes child headings") >= 3
    assert "chunk_cursor" in OP_CONSUMES["read"]
    assert "export" not in OP_CONSUMES["md_to_dict"]


def test_unmatched_hash_key_names_the_key(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "a.md").write_text("# A\n\na\n", encoding="utf-8")
    out = read_files_batch(
        ["a.md"],
        root=root,
        expected_read_sha256={"missing.md": "ab"},
    )
    assert out.get("code") == "param_not_applicable"
    assert "missing.md" in out["data"]["keys"]
