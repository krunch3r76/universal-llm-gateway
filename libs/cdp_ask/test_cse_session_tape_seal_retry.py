"""Retry-after-partial succession seal (friction:40105)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _listening_row() -> dict:
    return {
        "port": 9333,
        "chat_url": "https://claude.ai/cowork/cse_pred_retry",
        "parent_thread": "12286",
        "started_at": 1_792_224_619.0,
    }


@pytest.mark.asyncio
async def test_retry_after_partial_archive_conflict_supersedes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A divergent partial archive is superseded on retry; seal completes."""
    from pathlib import Path

    from chat_harvest.archive import ArchiveConflictError
    from chat_harvest.models import ConflictDetail

    from cdp_ask.cse_session_tape_seal import seal_predecessor_cse_to_tape

    calls: list[dict] = []

    def _archive(**kwargs: object) -> tuple[str, str, str]:
        calls.append(dict(kwargs))
        if len(calls) == 1:
            raise ArchiveConflictError(
                path=Path("notes/system/threads/x.messages.json"),
                existing_sha256="11ca944e" + "0" * 56,
                detail=ConflictDetail(
                    ordinal=12,
                    existing_digest="11ca944e" + "0" * 56,
                    new_digest="6c8463d8" + "0" * 56,
                    existing_len=10,
                    new_len=10,
                    existing_snippet="old",
                    new_snippet="new",
                ),
            )
        return ("cortex://notes/system/threads/x.messages.json", "abc", "first_write")

    monkeypatch.setattr("claude_bundles.cdp_lane.is_listening", lambda _port: True)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: {"rid-retry": _listening_row()},
    )

    async def _scrape(_port: int, _url: str, *, match_chat_url_only: bool = False) -> list[dict]:
        return [{"author": "user", "text": "partial then full", "ordinal": 1}]

    monkeypatch.setattr(
        "cdp_ask.cse_session_tape_seal._scrape_predecessor_turns",
        _scrape,
    )

    def _post(_payload: dict) -> MagicMock:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "transcript_path": "cortex://notes/system/threads/tape.md",
            "journal_row_id": "jr-1",
        }
        return response

    monkeypatch.setattr("cdp_ask.cse_session_tape_seal._post_session_close", _post)
    monkeypatch.setattr(
        "chat_harvest.archive.archive_chat_transcript",
        _archive,
    )
    result = await seal_predecessor_cse_to_tape("rid-retry")
    assert result["transcript_path"].endswith("tape.md")
    assert len(calls) == 2
    assert calls[0].get("supersede") is not True
    assert calls[1].get("supersede") is True
