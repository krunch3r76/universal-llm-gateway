"""S2 — session_summary_md_path offload (todo:session-close-light-latency)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_store.dispatch_ops._session_summary_path import (
    resolve_session_summary_md,
    summary_path_hint,
)
from cortex_store.dispatch_ops.adapters._doc_validate import _op_doc_validate
from cortex_store.dispatch_ops.ops_session_close import _op_session_close_preflight


@pytest.mark.offline
def test_resolve_session_summary_md_path_wins(tmp_path: Path) -> None:
    summary_file = tmp_path / "summary.md"
    summary_file.write_text("## Session Summary\n\nFrom path.\n", encoding="utf-8")
    text, err = resolve_session_summary_md(
        session_summary_md="## Session Summary\n\nInline ignored.\n",
        session_summary_md_path=str(summary_file.name),
        files_root=tmp_path,
    )
    assert err is None
    assert text is not None
    assert "From path" in text
    assert "Inline ignored" not in text


@pytest.mark.offline
def test_resolve_session_summary_md_sandbox_escape(tmp_path: Path) -> None:
    text, err = resolve_session_summary_md(
        session_summary_md=None,
        session_summary_md_path="../outside.md",
        files_root=tmp_path,
    )
    assert text is None
    assert err is not None
    assert err["reason"] == "session_summary_md_path.sandbox_escape"
    assert "files_root" in err
    assert "summary_path_hint" in err


@pytest.mark.offline
def test_resolve_accepts_cortex_uri(tmp_path: Path) -> None:
    nested = tmp_path / "notes" / "system" / "tmp"
    nested.mkdir(parents=True)
    (nested / "s.md").write_text("## Session Summary\n\nURI.\n", encoding="utf-8")
    text, err = resolve_session_summary_md(
        session_summary_md=None,
        session_summary_md_path="cortex://notes/system/tmp/s.md",
        files_root=tmp_path,
    )
    assert err is None
    assert text is not None and "URI" in text


@pytest.mark.offline
def test_resolve_accepts_absolute_under_files_root(tmp_path: Path) -> None:
    summary_file = tmp_path / "abs-summary.md"
    summary_file.write_text("## Session Summary\n\nAbs.\n", encoding="utf-8")
    text, err = resolve_session_summary_md(
        session_summary_md=None,
        session_summary_md_path=str(summary_file.resolve()),
        files_root=tmp_path,
    )
    assert err is None
    assert text is not None and "Abs" in text


@pytest.mark.offline
def test_resolve_outside_files_root_teaches(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-not-files" / "x.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("nope", encoding="utf-8")
    text, err = resolve_session_summary_md(
        session_summary_md=None,
        session_summary_md_path=str(outside.resolve()),
        files_root=tmp_path,
    )
    assert text is None
    assert err is not None
    assert err["reason"] == "session_summary_md_path.outside_files_root"
    assert "mcp-data/files" in err["hint"] or "CORTEX_FILES_ROOT" in err["hint"]


@pytest.mark.offline
def test_resolve_unreadable_teaches_resolved_path(tmp_path: Path) -> None:
    text, err = resolve_session_summary_md(
        session_summary_md=None,
        session_summary_md_path="notes/missing-summary.md",
        files_root=tmp_path,
    )
    assert text is None
    assert err is not None
    assert err["reason"] == "session_summary_md_path.unreadable"
    assert str(tmp_path.resolve()) in err["files_root"]
    assert "Prefer path params" in err["hint"]


@pytest.mark.offline
def test_summary_path_hint_shape() -> None:
    hint = summary_path_hint(session_id="cursor-2026-07-13-045300-261")
    assert hint["prefer"] == "session_summary_md_path"
    assert "cursor-2026-07-13-045300-261-summary.md" in hint["example_relative"]
    assert hint["example_uri"].startswith("cortex://")


@pytest.mark.offline
def test_preflight_accepts_session_summary_md_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary_file = tmp_path / "light-summary.md"
    summary_file.write_text(
        "## Session Summary\n\nPath-resolved light close summary.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._session_summary_path._FILES_ROOT",
        tmp_path,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_session_close._safe_run_audit",
        lambda **_: {},
    )
    result = _op_session_close_preflight(
        session_id="cursor-2026-07-09-120000-abc",
        agent="cursor",
        session_summary_md_path="light-summary.md",
        summary="Arc: path-resolved summary works for light preflight.",
        transcript_depth="light",
    )
    assert result.get("ok") is True
    assert result.get("reason") is None
    hint = result.get("summary_path_hint")
    assert isinstance(hint, dict)
    assert hint.get("prefer") == "session_summary_md_path"
    assert "cursor-2026-07-09-120000-abc" in str(hint.get("example_relative"))


@pytest.mark.offline
def test_doc_validate_accepts_session_summary_md_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary_file = tmp_path / "validate-summary.md"
    summary_file.write_text(
        "## Session Summary\n\nValidate via path.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._session_summary_path._FILES_ROOT",
        tmp_path,
    )

    def _fake_preflight(**kwargs: object) -> dict[str, object]:
        # Path is resolved in doc_validate before preflight; text must be present.
        assert "Validate via path" in str(kwargs.get("session_summary_md") or "")
        return {
            "ok": True,
            "session_id": "cursor-2026-07-09-120000-abc",
            "audit": {},
            "warnings": [],
            "turn_count": 0,
        }

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_session_close._op_session_close_preflight",
        _fake_preflight,
    )
    result = _op_doc_validate(
        doc_type="session_close",
        session_id="cursor-2026-07-09-120000-abc",
        agent="cursor",
        session_summary_md_path="validate-summary.md",
        summary="Arc: doc_validate accepts session_summary_md_path offload.",
        transcript_depth="light",
    )
    assert result["status"] == "pass"
