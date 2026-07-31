"""Unit tests for mapped-pack discovery (list_mapped)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools._rag_mapped import (  # noqa: E402
    LIST_MAPPED_ACTIVATION_NOTE,
    clear_index_cache,
    list_mapped_entries,
)

_SEEDED_SCOPES = frozenset({"legal_writing_samples", "cover_letter_samples"})


def setup_function() -> None:
    clear_index_cache()


def test_list_mapped_entries_seeded_keys_without_uri() -> None:
    entries = list_mapped_entries()
    assert len(entries) >= 2
    scopes = {entry["scope"] for entry in entries}
    assert _SEEDED_SCOPES <= scopes
    for entry in entries:
        assert "uri" not in entry
        assert "scope" in entry
        assert "query" in entry
        activate = entry["activate"]
        assert activate["op"] == "search"
        assert activate["mapped"] is True
        assert activate["scope"] == entry["scope"]
        assert activate["query"] == entry["query"]
        assert "label" not in entry


def test_list_mapped_activation_note_constant() -> None:
    assert "mapped=true" in LIST_MAPPED_ACTIVATION_NOTE
    assert "fs-read" in LIST_MAPPED_ACTIVATION_NOTE


def test_list_mapped_top_level_note_shape() -> None:
    entries = list_mapped_entries()
    payload = {
        "status": "ok",
        "entries": entries,
        "count": len(entries),
        "note": LIST_MAPPED_ACTIVATION_NOTE,
    }
    assert payload["note"]
    assert "Activation =" in payload["note"]
    assert payload["count"] >= 2


def test_list_mapped_entries_missing_index_empty(tmp_path: Path) -> None:
    """Missing index → warn+empty catalog (not hard error)."""
    missing = tmp_path / "does-not-exist-rag_mapped_index.yaml"
    assert not missing.exists()
    entries = list_mapped_entries(index_path=missing)
    assert entries == []
    payload = {
        "status": "ok",
        "entries": entries,
        "count": len(entries),
        "note": LIST_MAPPED_ACTIVATION_NOTE,
    }
    assert payload["count"] == 0
    assert payload["note"] == LIST_MAPPED_ACTIVATION_NOTE


def test_list_mapped_entries_empty_index_file(tmp_path: Path) -> None:
    """Readable index with no entries → empty catalog."""
    empty_index = tmp_path / "empty_rag_mapped_index.yaml"
    empty_index.write_text("entries: []\n", encoding="utf-8")
    entries = list_mapped_entries(index_path=empty_index)
    assert entries == []
