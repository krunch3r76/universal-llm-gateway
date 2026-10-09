"""session_close arguments, already_closed, sealed event surface."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cortex_store.session_close_validation import _validate_session_close_args

from . import seal as seal_mod

pytestmark = pytest.mark.offline

_PATH = "notes/system/transcripts/grok/hm1-grok-lan.transcribed.messages.json"
_SEALED_ON = "2026-01-02T03:04:05Z"


def _boot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import cortex_store.db as cortex_db
    from cortex_store._test_db_bootstrap import materialize_head_schema_template

    db_path = tmp_path / "cortex.db"
    materialize_head_schema_template(db_path)
    monkeypatch.setattr(cortex_db, "_CORTEX_DB", db_path)
    cortex_db.run_migrations(cortex_db.cortex_conn())


class _Clock:
    @staticmethod
    def now(tz=None):
        return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.mark.asyncio
async def test_seal_args_and_already_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _boot(monkeypatch, tmp_path)
    monkeypatch.setattr(seal_mod, "datetime", _Clock)
    from cortex_store.dispatch_ops import ops_journals

    calls: list[dict] = []
    events: list[dict] = []
    op_sig = inspect.signature(ops_journals._op_session_close)

    async def fake_dispatch(tool: str, args: dict) -> dict:
        op_sig.bind(**args)
        rejected = _validate_session_close_args(
            session_id=args["session_id"],
            agent=args["agent"],
            transcript_jsonl_path=None,
            session_summary_md=args["session_summary_md"],
            summary=args["summary"],
            transcript_messages_path=args["transcript_messages_path"],
            transcript_depth=args["transcript_depth"],
            emit_rejected=False,
        )
        assert rejected is None
        calls.append({"tool": tool, "args": args})
        return {"session_id": args["session_id"], "already_closed": True, "meta": {}}

    monkeypatch.setattr(
        seal_mod, "stargate_continuity_checkpoint_sealed", lambda **kw: events.append(kw)
    )
    out = await seal_mod.run_seal(
        {
            "bus_identity": "grok-bot-hm1",
            "root_thread": "9",
            "agent_id": "grok-lane-15903-unit",
            "house": "hm1",
            "n": 4,
        },
        {
            "snapshot_uri": _PATH,
            "surface": "grok",
            "turn_count": 2,
            "coverage": "full",
            "messages_sha256": "abc",
            "provenance": "transcribed",
            "producer": "harvester-bot-fixture",
        },
        execution_id="exec",
        dispatch=fake_dispatch,
    )
    args = calls[0]["args"]
    assert args["transcript_messages_path"] == _PATH
    assert args["closed_by"] == "succession"
    assert args["succession_seal_authority"] is True
    assert args["agent"] == "grok-bot-hm1"
    assert args["session_id"].startswith("grok-bot-")
    assert out["already_closed"] is True
    assert out["session_id"] == args["session_id"]
    from agent_seat.session_id import SESSION_ID_RE

    assert SESSION_ID_RE.fullmatch(args["session_id"])
    assert out["sealed_on"] == _SEALED_ON
    assert events[0]["surface"] == "grok"
    assert out["proposals"] == []


def test_lookup_and_fallback_share_one_session_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _boot(monkeypatch, tmp_path)
    import cortex_store.db as cortex_db

    house = "hm1"
    agent_id = "grok-lane-15903-unit"
    first = seal_mod._stable_session_id(house, 1, "aa" * 32, agent_id)
    second = seal_mod._stable_session_id(house, 9, "bb" * 32, agent_id)
    assert first == second
    conn = cortex_db.cortex_conn()
    conn.execute(
        "INSERT INTO session_journals (session_id, conversation_uuid, closed_by, "
        "agent, summary, timestamp) VALUES (?, ?, 'succession', ?, ?, ?)",
        (first, agent_id, "grok-bot", "sealed", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()
    assert seal_mod._lookup_session_id_any_closed_by(agent_id) == first


@pytest.mark.asyncio
async def test_lookup_failure_refuses_without_seal(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom():
        raise RuntimeError("cortex down")

    monkeypatch.setattr("cortex_store.db.cortex_conn", boom)
    calls: list[dict] = []
    events: list[dict] = []

    async def fake_dispatch(tool: str, args: dict) -> dict:
        calls.append(args)
        return {"session_id": "sess", "already_closed": False}

    monkeypatch.setattr(
        seal_mod, "stargate_continuity_checkpoint_sealed", lambda **kw: events.append(kw)
    )

    out = await seal_mod.run_seal(
        {
            "bus_identity": "grok-bot-hm1",
            "agent_id": "grok-lane-15903-unit",
            "house": "hm1",
        },
        {
            "snapshot_uri": _PATH,
            "messages_sha256": "abc",
            "surface": "grok",
        },
        execution_id="exec",
        dispatch=fake_dispatch,
    )
    assert out["refused"]["code"] == "seal.lookup_failed"
    assert "RuntimeError" in out["refused"]["message"]
    assert calls == []
    assert events == []
