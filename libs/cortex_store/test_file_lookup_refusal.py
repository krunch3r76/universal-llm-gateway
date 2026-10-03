"""Entity lookup of an existing cortex file names fs(op=read) instead of 404."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from cortex_store.entity_aliases import resolve_entity_reference
from cortex_store.file_lookup_refusal import FILE_LOOKUP_STATUS, file_lookup_refusal
from cortex_store.routes.resolve import resolve_cortex_uri

_URI = "cortex://notes/system/threads/example.md"
_READ = 'fs(op="read", path="cortex://notes/system/threads/example.md")'


def _note(root: Path) -> None:
    path = root / "notes" / "system" / "threads" / "example.md"
    path.parent.mkdir(parents=True)
    path.write_text("present\n", encoding="utf-8")


def test_scheme_uri_names_fs_read(tmp_path: Path) -> None:
    _note(tmp_path)
    detail = file_lookup_refusal(_URI, cortex_root=tmp_path)
    assert detail is not None
    assert _READ in detail
    assert "not found" not in detail.lower()


def test_entity_pointer_is_not_a_file(tmp_path: Path) -> None:
    assert (
        file_lookup_refusal(
            "cortex://decision/rag-phased-rollout", cortex_root=tmp_path
        )
        is None
    )


def test_plain_entity_id_is_not_a_file(tmp_path: Path) -> None:
    _note(tmp_path)
    assert file_lookup_refusal("todo:some-slug", cortex_root=tmp_path) is None


def test_colon_misparse_of_existing_file_names_fs(tmp_path: Path) -> None:
    _note(tmp_path)
    detail = file_lookup_refusal(
        "notes:system/threads/example.md", cortex_root=tmp_path
    )
    assert detail is not None
    assert _READ in detail


def test_colon_slash_without_file_stays_entity_lookup(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    assert (
        file_lookup_refusal("notes:system/threads/missing.md", cortex_root=tmp_path)
        is None
    )


def test_scheme_missing_file_stays_entity_lookup(tmp_path: Path) -> None:
    (tmp_path / "notes" / "system" / "threads").mkdir(parents=True)
    assert (
        file_lookup_refusal(
            "cortex://notes/system/threads/missing.md", cortex_root=tmp_path
        )
        is None
    )


def test_entity_type_matching_folder_is_not_a_file(tmp_path: Path) -> None:
    """agent-bus/ exists as a directory; cortex://agent-bus/7182 is an entity."""
    (tmp_path / "agent-bus" / "attachments").mkdir(parents=True)
    assert file_lookup_refusal("cortex://agent-bus/7182", cortex_root=tmp_path) is None


def test_overlong_component_does_not_raise(tmp_path: Path) -> None:
    """A 300-byte path component must not become HTTP 500."""
    (tmp_path / "notes" / "system" / "threads").mkdir(parents=True)
    detail = file_lookup_refusal(
        "notes:system/threads/" + ("A" * 300) + ".md",
        cortex_root=tmp_path,
    )
    assert detail is None


def test_traversal_is_not_refused(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    assert file_lookup_refusal("cortex://notes/../../x", cortex_root=tmp_path) is None


def test_empty_files_root_does_not_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    assert file_lookup_refusal(_URI) is None


def test_resolve_entity_reference_refuses_before_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _note(tmp_path)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    conn = sqlite3.connect(":memory:")
    with pytest.raises(HTTPException) as exc:
        resolve_entity_reference(conn, _URI)
    assert exc.value.status_code == FILE_LOOKUP_STATUS
    assert _READ in str(exc.value.detail)
    conn.close()


def test_resolve_uri_refuses_before_entity_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _note(tmp_path)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    with pytest.raises(HTTPException) as exc:
        resolve_cortex_uri(uri=_URI)
    assert exc.value.status_code == FILE_LOOKUP_STATUS
    assert _READ in str(exc.value.detail)


def test_resolve_entity_whose_type_matches_a_files_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Folder agent-bus/ exists; the entity row still resolves."""
    (tmp_path / "agent-bus").mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT, name TEXT)")
    conn.execute(
        "INSERT INTO entities (id, type, name) VALUES (?, ?, ?)",
        ("agent-bus:7182", "agent-bus", "7182"),
    )

    class _Conn:
        def __enter__(self) -> sqlite3.Connection:
            return conn

        def __exit__(self, *_exc: object) -> bool:
            return False

    monkeypatch.setattr("cortex_store.routes.resolve.cortex_conn", lambda: _Conn())
    result = resolve_cortex_uri(uri="cortex://agent-bus/7182", tag=None)
    assert result["resolved"] == "entity"
    assert result["entity"]["id"] == "agent-bus:7182"
    conn.close()
