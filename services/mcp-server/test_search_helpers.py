"""Tests for shared fs search helpers."""

from __future__ import annotations

from pathlib import Path


def test_is_search_binary_skips_compiled_artifacts(tmp_path: Path) -> None:
    from tools import _search_helpers

    assert _search_helpers.is_search_binary(tmp_path / "model.safetensors")
    assert _search_helpers.is_search_binary(tmp_path / "cache.db")
    assert not _search_helpers.is_search_binary(tmp_path / "note.md")


def test_load_text_for_search_file_skips_binary(tmp_path: Path) -> None:
    from tools import _search_helpers

    binary = tmp_path / "weights.safetensors"
    binary.write_bytes(b"\x00" * 32)
    state = _search_helpers.SearchBudgetState()
    text, method = _search_helpers.load_text_for_search_file(
        binary,
        state,
        budget_s=20.0,
        file_cap=10,
    )
    assert text is None
    assert method is None
    assert state.skipped_converted == 0
