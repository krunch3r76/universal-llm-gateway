"""Unit tests for handoff provenance helpers (agent-bus thread 1188).

Pure-function coverage for the additive ``handoff_provenance`` stamp — no DB,
so independent of the session-close fixture schema.
"""

from __future__ import annotations

from pathlib import Path

from cortex_store.session_handoff import (
    WRITE_PATH_HANDOFF_UPSERT,
    WRITE_PATH_SESSION_CLOSE,
    build_handoff_provenance,
    check_handoff_prompt_in_source,
    compute_source_file_sha256,
    merge_handoff_attribute,
)


def test_provenance_with_source_file_hashes_content(tmp_path: Path) -> None:
    src = tmp_path / "notes" / "h.md"
    src.parent.mkdir(parents=True)
    src.write_text("next decision: G2 policy", encoding="utf-8")

    prov = build_handoff_provenance(
        write_path=WRITE_PATH_SESSION_CLOSE,
        source_path="cortex:notes/h.md",
        files_root=tmp_path,
        written_at="2026-06-02T20:00:00Z",
    )

    assert prov["write_path"] == "session_close"
    assert prov["written_at"] == "2026-06-02T20:00:00Z"
    assert prov["source_file"] == "notes/h.md"
    assert prov["source_file_sha256"].startswith("sha256:")


def test_provenance_without_source_file_is_null(tmp_path: Path) -> None:
    prov = build_handoff_provenance(
        write_path=WRITE_PATH_HANDOFF_UPSERT,
        source_path=None,
        files_root=tmp_path,
    )
    assert prov["write_path"] == "session_handoff_upsert"
    assert prov["source_file"] is None
    assert prov["source_file_sha256"] is None


def test_missing_file_degrades_to_null_hash(tmp_path: Path) -> None:
    assert compute_source_file_sha256(tmp_path, "notes/absent.md") is None


def test_path_traversal_escape_yields_null_hash(tmp_path: Path) -> None:
    (tmp_path.parent / "secret.md").write_text("x", encoding="utf-8")
    assert compute_source_file_sha256(tmp_path, "../secret.md") is None


def test_merge_sets_and_clears_provenance() -> None:
    prov = {"write_path": "session_close"}
    with_prompt = merge_handoff_attribute({}, "do X", prov)
    assert with_prompt["handoff_prompt"] == "do X"
    assert with_prompt["handoff_provenance"] == prov

    cleared = merge_handoff_attribute(with_prompt, None)
    assert "handoff_prompt" not in cleared
    assert "handoff_provenance" not in cleared


def test_check_passes_when_prompt_is_substring(tmp_path: Path) -> None:
    src = tmp_path / "notes" / "h.md"
    src.parent.mkdir(parents=True)
    src.write_text("# Handoff\n\nnext decision: G2 policy\n", encoding="utf-8")

    assert (
        check_handoff_prompt_in_source(
            handoff_prompt="next decision: G2 policy",
            source_path="cortex:notes/h.md",
            files_root=tmp_path,
        )
        is None
    )


def test_check_flags_prompt_not_in_source(tmp_path: Path) -> None:
    src = tmp_path / "notes" / "h.md"
    src.parent.mkdir(parents=True)
    src.write_text("# Handoff\n\nthe real plan\n", encoding="utf-8")

    finding = check_handoff_prompt_in_source(
        handoff_prompt="a drifted, independently-authored line",
        source_path="notes/h.md",
        files_root=tmp_path,
    )
    assert finding is not None
    assert finding["kind"] == "handoff_prompt_source_mismatch"
    assert finding["severity"] == "warning"


def test_check_flags_unreadable_source(tmp_path: Path) -> None:
    finding = check_handoff_prompt_in_source(
        handoff_prompt="any prompt",
        source_path="notes/absent.md",
        files_root=tmp_path,
    )
    assert finding is not None
    assert finding["kind"] == "handoff_prompt_source_mismatch"


def test_check_noop_when_no_source_path(tmp_path: Path) -> None:
    # Detached-string handoff (no source_path) is the surface-but-flag
    # case handled by provenance source_file:null — not a mismatch.
    assert (
        check_handoff_prompt_in_source(
            handoff_prompt="detached line",
            source_path=None,
            files_root=tmp_path,
        )
        is None
    )


def test_check_noop_when_no_prompt(tmp_path: Path) -> None:
    assert (
        check_handoff_prompt_in_source(
            handoff_prompt=None,
            source_path="notes/h.md",
            files_root=tmp_path,
        )
        is None
    )
