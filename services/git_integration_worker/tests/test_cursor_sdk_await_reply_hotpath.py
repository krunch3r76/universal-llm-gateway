"""Await-reply bus dedupe regression guards (not the tail-poller fix)."""

from __future__ import annotations

import json

import pytest

from services.git_integration_worker.cursor_sdk_await_reply import (
    AwaitedGenerate,
    _snapshot_replies,
)


@pytest.mark.asyncio
async def test_snapshot_replies_dedupes_thread_after_turn_per_tick() -> None:
    calls: list[tuple[str, int]] = []

    def turns_fn(thread_id: str, after_turn: int) -> list[dict]:
        calls.append((thread_id, after_turn))
        return []

    awaited = [
        AwaitedGenerate(
            execution_id="e1",
            thread_id="22613",
            after_turn=3,
            from_agent="web",
            model=None,
            fired_at="",
        ),
        AwaitedGenerate(
            execution_id="e2",
            thread_id="22613",
            after_turn=3,
            from_agent="web",
            model=None,
            fired_at="",
        ),
    ]
    await _snapshot_replies(awaited, bus_turns_fn=turns_fn)
    assert calls == [("22613", 3)]


@pytest.mark.asyncio
async def test_resume_tick_dedupes_turns_fetch_across_park_rows(monkeypatch) -> None:
    """Resume pass shares one GET /turns per (thread, after_turn) per tick."""
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from services.git_integration_worker.cursor_sdk_await_reply import (
        AwaitResumeSummary,
        _resume_await_parked_loop,
    )
    from services.git_integration_worker.cursor_sdk_park_ledger import ParkRow

    rows = [
        ParkRow(
            dispatch_id="park-a",
            thread_id="house",
            execution_id="e",
            caller_agent="a",
            resolved_model="m",
            status="completed",
            terminal_status="completed",
            sdk_agent_id=None,
            state_root=None,
            source_ref=None,
            work_key=None,
            contract="consult",
            packet_path=None,
            park_kind="await_cdp_reply",
            park_intent_id=None,
            parked_at=datetime.now(UTC).isoformat(),
            park_resumed_by=None,
            park_expires_at=None,
            record_json=json.dumps(
                {
                    "park": {
                        "awaited": [
                            {
                                "execution_id": "ex-1",
                                "thread_id": "22613",
                                "after_turn": 2,
                                "from_agent": "web",
                            }
                        ]
                    }
                }
            ),
            source_repo="/repo",
        ),
        ParkRow(
            dispatch_id="park-b",
            thread_id="house",
            execution_id="e2",
            caller_agent="a",
            resolved_model="m",
            status="completed",
            terminal_status="completed",
            sdk_agent_id=None,
            state_root=None,
            source_ref=None,
            work_key=None,
            contract="consult",
            packet_path=None,
            park_kind="await_cdp_reply",
            park_intent_id=None,
            parked_at=datetime.now(UTC).isoformat(),
            park_resumed_by=None,
            park_expires_at=None,
            record_json=json.dumps(
                {
                    "park": {
                        "awaited": [
                            {
                                "execution_id": "ex-2",
                                "thread_id": "22613",
                                "after_turn": 2,
                                "from_agent": "web",
                            }
                        ]
                    }
                }
            ),
            source_repo="/repo",
        ),
    ]
    calls: list[tuple[str, int]] = []

    def turns_fn(thread_id: str, after_turn: int) -> list[dict]:
        calls.append((thread_id, after_turn))
        return []

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_await_reply.open_await_rows",
        lambda: rows,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_await_reply._is_conductor_dispatch",
        lambda _d: False,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_await_reply._usable_resume_child",
        lambda _d: None,
    )

    summary = AwaitResumeSummary()
    controller = SimpleNamespace(is_draining=lambda: False)
    await _resume_await_parked_loop(
        bus_turns_fn=turns_fn,
        summary=summary,
        controller=controller,
        bus=AsyncMock(),
        cfg=SimpleNamespace(source_repo="/repo"),
        code_version="v",
        ledger=AsyncMock(),
        ttl=60,
        clock=datetime.now(UTC),
        parent_dispatch_id=None,
    )
    assert calls == [("22613", 2)]


def test_open_await_rows_uses_json_extract_not_substring(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
        _connect,
    )
    from services.git_integration_worker.cursor_sdk_await_reply import (
        PARK_KIND_AWAIT_REPLY,
        open_await_rows,
    )

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    CursorDispatchLedger.instance()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, resolved_model, status, "
            "park_kind, message_present, read_only, record_json) "
            "VALUES ('stray-substr', 'fp', 't', 'm', 'completed', ?, 0, 0, ?)",
            (
                PARK_KIND_AWAIT_REPLY,
                json.dumps(
                    {
                        "message": 'mention "expired_at" in prose only',
                        "park": {"awaited": []},
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, resolved_model, status, "
            "park_kind, message_present, read_only, record_json) "
            "VALUES ('park-expired', 'fp2', 't', 'm', 'completed', ?, 0, 0, ?)",
            (
                PARK_KIND_AWAIT_REPLY,
                json.dumps({"park": {"expired_at": "2020-01-01T00:00:00+00:00"}}),
            ),
        )
    ids = {r.dispatch_id for r in open_await_rows()}
    assert "stray-substr" in ids
    assert "park-expired" not in ids


