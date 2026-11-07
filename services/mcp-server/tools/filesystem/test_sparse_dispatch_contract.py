"""Dispatcher-level sparse read contract (corrective leg)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools._file_helpers import read_file_result, read_files_batch
from tools._hashing import sha256_hex_of_file
from tools.filesystem._fs_dispatch import (
    OP_CONSUMES,
    md_section_op_doc,
    validate_op_params,
)
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


def test_workspaces_share_uri_matches_repo_relative_path(tmp_path: Path) -> None:
    """workspaces://{repo}/{rel} matches sandbox-relative {repo}/{rel}."""
    root = _root(tmp_path)
    rel = "universal-llm-gateway/notes/doc.md"
    target = root / rel
    target.parent.mkdir(parents=True)
    target.write_text("# Title\n\nbody\n", encoding="utf-8")
    live = sha256_hex_of_file(target)
    out = read_files_batch(
        [rel],
        root=root,
        sections=["Title"],
        expected_read_sha256={
            "workspaces://universal-llm-gateway/notes/doc.md": live
        },
    )
    assert out.get("code") != "param_not_applicable"
    item = out["files"][rel]["items"][0]
    assert item["outcome"] == "ok"
    assert "body" in item["content"]


def test_single_path_read_multi_returns_manifest_descriptors(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "doc.md").write_text("# Title\n\nbody\n", encoding="utf-8")
    batch = read_files_batch(["doc.md"], root=root)
    entry = batch["files"]["doc.md"]
    assert entry["selection"] == "manifest"
    assert "content" not in entry
    assert entry.get("code") != "sparse_manifest_requires_expansion"
    assert any(row.get("heading") == "Title" or row.get("id") == "Title" for row in entry["sections"])


def test_read_multi_export_stops_later_paths(tmp_path: Path) -> None:
    """Export end-the-batch across files. Not the one-document sections loop."""
    root = _root(tmp_path)
    big = "Z" * 70000
    for name, body in (
        ("a.md", "# A\n\na\n"),
        ("b.md", "# B\n\nb\n"),
        ("big.md", f"# Big\n\n{big}\n"),
        ("c.md", "# C\n\nc\n"),
    ):
        (root / name).write_text(body, encoding="utf-8")
    paths = ["a.md", "b.md", "big.md", "c.md"]
    batch = read_files_batch(paths, root=root, export=True)
    items = batch["items"]
    assert [item["path"] for item in items] == paths
    assert [item["outcome"] for item in items] == [
        "ok",
        "ok",
        "over_budget",
        "over_budget",
    ]
    assert "content" not in items[2]
    assert "content" not in items[3]
    assert sum(1 for item in items if item["path"] == "big.md") == 1
    assert items[2].get("oversize") is True
    assert "oversize" not in items[3]
    assert batch["budget_cursor"] == 2
    assert batch["omitted"] == ["big.md", "c.md"]


def test_byte_ceiling_boundary_and_over(tmp_path: Path) -> None:
    from tools._sparse_document_read import (
        SPARSE_MAX_CONTENT_BYTES,
        finalize_sparse_document_payload,
    )

    assert SPARSE_MAX_CONTENT_BYTES == 262144
    unit = "\U0001f600"
    assert len(unit.encode("utf-8")) == 4
    exact = unit * (SPARSE_MAX_CONTENT_BYTES // 4)
    held = finalize_sparse_document_payload(
        {"selection": "section", "content": exact, "path": "a.md"}
    )
    assert held.get("code") != "sparse_payload_over_byte_ceiling"
    assert held.get("sparse_policy")
    over = finalize_sparse_document_payload(
        {"selection": "section", "content": exact + "x", "path": "a.md"}
    )
    assert over["code"] == "sparse_payload_over_byte_ceiling"

    root = _root(tmp_path)
    # One code point over the char cap forces the chunk path. The returned
    # chunk is 65536 four-byte characters, exactly the 262144-byte boundary.
    (root / "wide.md").write_text(
        "# Wide\n\n" + (exact + unit) + "\n",
        encoding="utf-8",
    )
    read = read_file_result(
        "wide.md",
        root=root,
        section="Wide",
        chunk_cursor=1,
        chunk_cursor_provided=True,
    )
    assert read.get("code") != "sparse_payload_over_byte_ceiling"
    assert read["selection"] == "section"
    assert len(read["content"]) == 65536
    assert len(read["content"].encode("utf-8")) == SPARSE_MAX_CONTENT_BYTES


def test_read_multi_consumes_every_declared_param(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    _patch_read(monkeypatch, root)
    (root / "a.md").write_text("# A\n\nalpha\n", encoding="utf-8")
    assert OP_CONSUMES["read_multi"] == frozenset(
        {"section", "sections", "export", "chunk_cursor", "expected_read_sha256"}
    )
    assert validate_op_params("read_multi", {"section": "A"}) is None

    by_section = read_files_batch_impl(["a.md"], section="A")
    assert by_section["files"]["a.md"]["selection"] == "section"
    assert "alpha" in by_section["files"]["a.md"]["content"]

    by_sections = read_files_batch_impl(["a.md"], sections=["A"])
    assert by_sections["files"]["a.md"]["items"][0]["outcome"] == "ok"

    exported = read_files_batch_impl(["a.md"], export=True)
    assert "alpha" in exported["files"]["a.md"]["content"]

    stale = read_files_batch_impl(["a.md"], expected_read_sha256="ab")
    assert stale["files"]["a.md"].get("outcome") == "stale_handle" or stale[
        "files"
    ]["a.md"].get("code") == "stale_handle"

    cursor = read_files_batch_impl(["a.md"], chunk_cursor=0)
    assert cursor.get("code") == "param_not_applicable"
    assert cursor["data"]["parameter"] == "chunk_cursor"


def test_xml_preamble_unsectioned_and_parent_metadata(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "mix.md").write_text(
        "front matter\n\n<scope>\ninside scope\n</scope>\n\n"
        "# Parent\n\nown\n\n## Child\n\nnested heading text\n",
        encoding="utf-8",
    )
    (root / "plain.md").write_text("just text\n", encoding="utf-8")
    preamble = read_file_result("mix.md", root=root, section="__preamble__")
    assert preamble["outcome"] == "ok"
    assert preamble["content"].startswith("front matter")
    xml = read_file_result("mix.md", root=root, section="<scope>")
    assert xml["outcome"] == "ok"
    assert "inside scope" in xml["content"]
    assert xml["includes_children"] is False
    parent = read_file_result("mix.md", root=root, section="Parent")
    assert "nested heading text" not in parent["content"]
    assert "Parent/Child" in parent["children"]
    plain = read_file_result("plain.md", root=root, section="__unsectioned__")
    assert plain["outcome"] == "ok"
    assert "just text" in plain["content"]
    assert plain["char_count"] == len(plain["content"])


def _own(text: str) -> str:
    return text.strip("\n")


def test_one_document_sections_termination_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance: one document, sections=[a,b,big,c], cumulative then oversize.

    a and b are ~40000 code points so b crosses the cap (budget_cursor=1).
    big exceeds 65536 and must be oversize on that first call even though b
    stopped the batch. c is small and stays over_budget without oversize.
    """
    root = _root(tmp_path)
    _patch_read(monkeypatch, root)
    a_body = "a" * 40000
    b_body = "b" * 40000
    big_body = "Z" * 65537
    c_body = "c"
    (root / "doc.md").write_text(
        f"# A\n\n{a_body}\n\n# B\n\n{b_body}\n\n# Big\n\n{big_body}\n\n# C\n\n{c_body}\n",
        encoding="utf-8",
    )
    calls: list[tuple] = [("sections", ["A", "B", "Big", "C"])]
    first = read_file_impl("doc.md", sections=["A", "B", "Big", "C"])
    assert first["selection"] == "sections"
    assert first["budget_cursor"] == 1
    assert [item["id"] for item in first["items"]] == ["A", "B", "Big", "C"]
    assert [item["outcome"] for item in first["items"]] == [
        "ok",
        "over_budget",
        "over_budget",
        "over_budget",
    ]
    assert "oversize" not in first["items"][1]
    assert first["items"][2]["oversize"] is True
    assert first["items"][2]["char_count"] > 65536
    assert first["items"][2]["fetch"] == {"section": "Big", "chunk_cursor": 0}
    assert "content" not in first["items"][2]
    assert "oversize" not in first["items"][3]
    assert "content" not in first["items"][3]

    delivered: dict[str, str] = {}

    def _absorb(payload: dict) -> tuple[list[str], list[str]]:
        pending: list[str] = []
        oversized: list[str] = []
        for item in payload["items"]:
            sid = item["id"]
            if item["outcome"] == "ok":
                assert sid not in delivered
                delivered[sid] = item["content"]
            elif item.get("oversize") is True:
                oversized.append(sid)
            else:
                pending.append(sid)
        return pending, oversized

    pending, oversized = _absorb(first)
    assert oversized == ["Big"]
    parts: list[str] = []
    cursor = 0
    while True:
        calls.append(("chunk", "Big", cursor))
        chunk = read_file_impl("doc.md", section="Big", chunk_cursor=cursor)
        assert chunk["outcome"] == "ok"
        assert chunk["selection"] == "section"
        parts.append(chunk["content"])
        nxt = chunk.get("chunk_cursor_next")
        if nxt is None:
            assert chunk["truncated"] is False
            break
        assert isinstance(nxt, int) and nxt > cursor
        cursor = nxt
    assert "Big" not in delivered
    delivered["Big"] = "".join(parts)
    assert pending == ["B", "C"]
    calls.append(("sections", ["B", "C"]))
    second = read_file_impl("doc.md", sections=["B", "C"])
    pending2, oversized2 = _absorb(second)
    assert pending2 == []
    assert oversized2 == []
    assert set(delivered) == {"A", "B", "Big", "C"}
    assert _own(delivered["A"]) == a_body
    assert _own(delivered["B"]) == b_body
    assert _own(delivered["Big"]) == big_body
    assert _own(delivered["C"]) == c_body
    assert len(calls) <= 6
    assert sum(1 for kind, *_rest in calls if kind == "chunk") >= 2


def test_budget_cursor_count_cap_and_cumulative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    _patch_read(monkeypatch, root)
    seventeen = "".join(f"# S{i}\n\nv{i}\n\n" for i in range(17))
    (root / "many.md").write_text(seventeen, encoding="utf-8")
    ids = [f"S{i}" for i in range(17)]
    counted = read_file_impl("many.md", sections=ids)
    assert counted["budget_cursor"] == 16
    assert [item["outcome"] for item in counted["items"][:16]] == ["ok"] * 16
    assert counted["items"][16]["outcome"] == "over_budget"
    assert "content" not in counted["items"][16]
    assert "oversize" not in counted["items"][16]

    (root / "pair.md").write_text(
        "# A\n\n" + ("a" * 40000) + "\n\n# B\n\n" + ("b" * 40000) + "\n",
        encoding="utf-8",
    )
    crossed = read_file_impl("pair.md", sections=["A", "B"])
    assert crossed["budget_cursor"] == 1
    assert crossed["items"][0]["outcome"] == "ok"
    assert crossed["items"][1]["outcome"] == "over_budget"
    assert "oversize" not in crossed["items"][1]


def test_read_multi_budget_cursor_is_flattened_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    _patch_read(monkeypatch, root)
    body = "# A\n\n" + ("a" * 40000) + "\n\n# B\n\n" + ("b" * 40000) + "\n"
    (root / "a.md").write_text(body, encoding="utf-8")
    (root / "b.md").write_text(body, encoding="utf-8")
    batch = read_files_batch_impl(["a.md", "b.md"], sections=["A", "B"])
    assert batch["budget_cursor"] == 1
    assert [(item["path"], item["id"]) for item in batch["items"]] == [
        ("a.md", "A"),
        ("a.md", "B"),
        ("b.md", "A"),
        ("b.md", "B"),
    ]
    assert [item["outcome"] for item in batch["items"]] == [
        "ok",
        "over_budget",
        "over_budget",
        "over_budget",
    ]
    assert "oversize" not in batch["items"][1]


def test_read_multi_export_later_sibling_oversize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later file that is itself over 65536 stays oversize after the stop."""
    root = _root(tmp_path)
    _patch_read(monkeypatch, root)
    (root / "a.md").write_text("# A\n\n" + ("a" * 40000) + "\n", encoding="utf-8")
    (root / "b.md").write_text("# B\n\n" + ("b" * 40000) + "\n", encoding="utf-8")
    (root / "huge.md").write_text("# H\n\n" + ("Z" * 70000) + "\n", encoding="utf-8")
    batch = read_files_batch_impl(["a.md", "b.md", "huge.md"], export=True)
    assert [item["outcome"] for item in batch["items"]] == [
        "ok",
        "over_budget",
        "over_budget",
    ]
    assert "oversize" not in batch["items"][1]
    assert batch["items"][2]["oversize"] is True
    assert batch["items"][2]["fetch"]["path"] == "huge.md"
    assert "content" not in batch["items"][1]
    assert "content" not in batch["items"][2]
    assert batch["budget_cursor"] == 1
    assert batch["omitted"] == ["b.md", "huge.md"]


def test_fs_dispatcher_workspaces_share_uri_matches_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ingress plus workspaces read_multi, not the helper alone."""
    from fs_impl import fs_impl

    projects = tmp_path / "projects"
    rel = "universal-llm-gateway/notes/doc.md"
    target = projects / rel
    target.parent.mkdir(parents=True)
    target.write_text("# Title\n\nbody\n", encoding="utf-8")
    live = sha256_hex_of_file(target)

    def _root(surface: str, root_id: str) -> Path:
        assert root_id == "workspaces"
        return projects

    monkeypatch.setattr("fs_roots.fs_root_for", _root)
    uri = "workspaces://universal-llm-gateway/notes/doc.md"
    common = {
        "surface": "code",
        "overflow_registry": {},
        "op": "read_multi",
        "sandbox": "workspaces",
        "path": "",
        "content": "",
        "target": "",
        "target_sandbox": "",
        "line": 0,
        "section": "",
        "all_occurrences": False,
        "include_untracked": True,
        "binary": False,
        "max_depth": 3,
        "offset": 0,
        "limit": 0,
        "expected_sha256": "",
        "if_absent": False,
        "paths": [rel],
        "sections": ["Title"],
    }
    matched = fs_impl(**common, expected_read_sha256={uri: live})
    assert matched.get("code") != "param_not_applicable", matched
    assert "error" not in matched, matched
    item = matched["files"][rel]["items"][0]
    assert item["outcome"] == "ok"
    assert "body" in item["content"]

    stale = fs_impl(**common, expected_read_sha256={uri: "0" * 64})
    entry = stale["files"][rel]
    assert entry.get("outcome") == "stale_handle" or entry.get("code") == "stale_handle"
    assert "content" not in entry
    assert entry.get("read_sha256") == live or (
        isinstance(entry.get("items"), list)
        and entry["items"]
        and entry["items"][0].get("outcome") == "stale_handle"
    )


def _replay_export_fetch(path: str, root: Path) -> str:
    """Replay one emitted export fetch until the final chunk."""
    live = sha256_hex_of_file(root / path)
    original = (root / path).read_text(encoding="utf-8")
    cursor = 0
    parts: list[str] = []
    for _ in range(8):
        chunk = read_file_result(
            path,
            root=root,
            export=True,
            chunk_cursor=cursor,
            chunk_cursor_provided=True,
            expected_read_sha256=live,
        )
        assert chunk.get("code") is None, chunk
        assert "content" in chunk
        parts.append(chunk["content"])
        nxt = chunk.get("chunk_cursor_next")
        if nxt is None:
            assert chunk.get("truncated") is False
            break
        assert isinstance(nxt, int) and nxt > cursor
        cursor = nxt
    else:
        raise AssertionError(f"export replay did not terminate for {path}")
    assert "".join(parts) == original
    return original


def _assert_export_replay(batch: dict, root: Path) -> None:
    emitted = [item for item in batch["items"] if isinstance(item.get("fetch"), dict)]
    assert emitted, "expected at least one document export fetch"
    for item in emitted:
        assert item["outcome"] == "over_budget"
        assert "content" not in item
        fetch = item["fetch"]
        assert fetch["export"] is True
        assert fetch["chunk_cursor"] == 0
        assert fetch["op"] == "read"
        assert fetch["path"] == item["path"]
        _replay_export_fetch(item["path"], root)


def test_read_multi_export_nondoc_after_stop_and_replay(tmp_path: Path) -> None:
    """a.md and b.md are 40006 chars; small.txt follows the stop and stays whole."""
    root = _root(tmp_path)
    a_text = "a" * 40006
    b_text = "b" * 40006
    small = "s" * 12
    (root / "a.md").write_text(a_text, encoding="utf-8")
    (root / "b.md").write_text(b_text, encoding="utf-8")
    (root / "small.txt").write_text(small, encoding="utf-8")
    batch = read_files_batch(["a.md", "b.md", "small.txt"], root=root, export=True)
    items = batch["items"]
    assert [item["path"] for item in items] == ["a.md", "b.md", "small.txt"]
    assert [item["outcome"] for item in items] == ["ok", "over_budget", "ok"]
    assert items[0]["content"] == a_text
    assert "content" not in items[1]
    assert "fetch" not in items[2]
    assert items[2]["content"] == small
    assert batch["budget_cursor"] == 1
    assert batch["omitted"] == ["b.md"]
    _assert_export_replay(batch, root)


def test_read_multi_export_big_txt_single_error_multi_full(
    tmp_path: Path,
) -> None:
    """Single-path export on a non-document stays param_not_applicable.

    A multi-path call returns the 70000-char body and does not budget it.
    """
    root = _root(tmp_path)
    big = "t" * 70000
    (root / "big.txt").write_text(big, encoding="utf-8")
    (root / "a.md").write_text("a" * 40006, encoding="utf-8")
    (root / "c.md").write_text("c" * 20000, encoding="utf-8")
    single = read_files_batch(["big.txt"], root=root, export=True)
    assert single["files"]["big.txt"]["code"] == "param_not_applicable"
    assert single["files"]["big.txt"]["data"]["parameter"] == "export"

    paired = read_files_batch(["big.txt", "a.md"], root=root, export=True)
    assert "omitted" not in paired
    assert "budget_cursor" not in paired
    assert paired["items"][0]["content"] == big
    assert "fetch" not in paired["items"][0]
    assert paired["items"][1]["outcome"] == "ok"
    assert paired["items"][1]["content"] == "a" * 40006

    mixed = read_files_batch(["a.md", "big.txt", "c.md"], root=root, export=True)
    assert [item["outcome"] for item in mixed["items"]] == ["ok", "ok", "ok"]
    assert mixed["items"][1]["content"] == big
    assert "fetch" not in mixed["items"][1]
    assert "omitted" not in mixed
    assert "budget_cursor" not in mixed
    assert len(mixed["items"][0]["content"]) + len(mixed["items"][2]["content"]) <= 65536


def test_read_multi_export_budget_markers(tmp_path: Path) -> None:
    """No omission, cumulative index, later oversize, count cap, mixed order."""
    root = _root(tmp_path)
    (root / "a.md").write_text("a" * 10, encoding="utf-8")
    (root / "b.md").write_text("b" * 10, encoding="utf-8")
    (root / "note.txt").write_text("n" * 12, encoding="utf-8")
    quiet = read_files_batch(["a.md", "note.txt", "b.md"], root=root, export=True)
    assert "omitted" not in quiet
    assert "budget_cursor" not in quiet
    assert quiet["items"][1]["content"] == "n" * 12

    (root / "a.md").write_text("a" * 40006, encoding="utf-8")
    (root / "b.md").write_text("b" * 40006, encoding="utf-8")
    (root / "huge.md").write_text("Z" * 70000, encoding="utf-8")
    (root / "tail.txt").write_text("tail-body!!", encoding="utf-8")
    crossed = read_files_batch(
        ["note.txt", "a.md", "b.md", "huge.md", "tail.txt"],
        root=root,
        export=True,
    )
    assert [item["path"] for item in crossed["items"]] == [
        "note.txt",
        "a.md",
        "b.md",
        "huge.md",
        "tail.txt",
    ]
    assert [item["outcome"] for item in crossed["items"]] == [
        "ok",
        "ok",
        "over_budget",
        "over_budget",
        "ok",
    ]
    assert crossed["items"][0]["content"] == "n" * 12
    assert crossed["items"][4]["content"] == "tail-body!!"
    assert "fetch" not in crossed["items"][0]
    assert "fetch" not in crossed["items"][4]
    assert "oversize" not in crossed["items"][2]
    assert crossed["items"][3]["oversize"] is True
    assert crossed["budget_cursor"] == 2
    assert crossed["omitted"] == ["b.md", "huge.md"]
    _assert_export_replay(crossed, root)

    for i in range(17):
        (root / f"d{i:02d}.md").write_text(f"d{i:02d}", encoding="utf-8")
    counted_paths = ["d00.md", "note.txt"] + [f"d{i:02d}.md" for i in range(1, 17)]
    counted = read_files_batch(counted_paths, root=root, export=True)
    assert counted["budget_cursor"] == 17
    assert counted["omitted"] == ["d16.md"]
    assert [item["outcome"] for item in counted["items"][:17]] == ["ok"] * 17
    assert counted["items"][1]["content"] == "n" * 12
    assert "fetch" not in counted["items"][1]
    assert counted["items"][17]["outcome"] == "over_budget"
    assert "content" not in counted["items"][17]
    assert "oversize" not in counted["items"][17]
    assert (
        sum(
            1
            for item in counted["items"]
            if item["outcome"] == "ok" and item["path"].endswith(".md")
        )
        == 16
    )


def test_fs_dispatcher_export_nondoc_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fs ingress read_multi export, not the helper alone."""
    from fs_impl import fs_impl

    projects = tmp_path / "projects"
    prefix = "universal-llm-gateway/notes"
    folder = projects / prefix
    folder.mkdir(parents=True)
    a_text = "a" * 40006
    b_text = "b" * 40006
    small = "s" * 12
    (folder / "a.md").write_text(a_text, encoding="utf-8")
    (folder / "b.md").write_text(b_text, encoding="utf-8")
    (folder / "small.txt").write_text(small, encoding="utf-8")
    paths = [f"{prefix}/a.md", f"{prefix}/b.md", f"{prefix}/small.txt"]

    def _fs_root(surface: str, root_id: str) -> Path:
        assert root_id == "workspaces"
        return projects

    monkeypatch.setattr("fs_roots.fs_root_for", _fs_root)
    out = fs_impl(
        surface="code",
        overflow_registry={},
        op="read_multi",
        sandbox="workspaces",
        path="",
        content="",
        target="",
        target_sandbox="",
        line=0,
        section="",
        all_occurrences=False,
        include_untracked=True,
        binary=False,
        max_depth=3,
        offset=0,
        limit=0,
        expected_sha256="",
        if_absent=False,
        paths=paths,
        export=True,
    )
    assert out.get("code") is None, out
    assert out.get("selection") == "multi"
    assert out["budget_cursor"] == 1
    assert out["omitted"] == paths[1:2]
    assert out["items"][2]["content"] == small
    assert "fetch" not in out["items"][2]
    assert out["items"][1]["outcome"] == "over_budget"
    assert "content" not in out["items"][1]
