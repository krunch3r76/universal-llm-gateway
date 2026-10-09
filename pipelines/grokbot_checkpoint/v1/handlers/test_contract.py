"""In-process session_close contract for grokbot-checkpoint-v1."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from cortex_store.dispatch_ops.ops_entities import _op_entity_get
from cortex_store.dispatch_ops.ops_session_close import _op_session_close
from systems.pipeline.core.executor.input_extraction import (
    extract_dispatch_thread_id,
)

from .envelope import run_envelope, staging_uri
from .seal import run_seal

pytestmark = pytest.mark.offline

_START = "2026-04-05T06:07:08Z"
_URI = "ephemeral/harvests/grok/hm1-agent-co.raw.messages.json"


def _boot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import cortex_store.db as cortex_db
    import cortex_store.dispatch_ops._shared as shared
    import cortex_store.dispatch_ops.ops_session_close as ops
    import cortex_store.routes.session_close_persist as persist
    import cortex_store.routes.session_journals as journals
    from cortex_store._test_db_bootstrap import materialize_head_schema_template

    for module in (shared, ops, persist, journals):
        monkeypatch.setattr(module, "_FILES_ROOT", tmp_path)
    db_path = tmp_path / "cortex.db"
    materialize_head_schema_template(db_path)
    monkeypatch.setattr(cortex_db, "_CORTEX_DB", db_path)
    cortex_db.run_migrations(cortex_db.cortex_conn())


def _messages(n: int, diverged: bool = False) -> list[dict]:
    rows = [
        {"role": "user", "content": "turn one", "turn_index": 1, "timestamp": _START},
        {"role": "assistant", "content": "ack one", "turn_index": 1},
    ]
    if n >= 2 and not diverged:
        rows.append({"role": "user", "content": "turn two", "turn_index": 2})
    if diverged:
        rows[1] = {"role": "assistant", "content": "different", "turn_index": 1}
        rows.append({"role": "user", "content": "turn two", "turn_index": 2})
    return rows


def _envelope(messages: list[dict]) -> dict:
    from continuity_tape.messages import seal_messages_sha256

    return {
        "schema": "ulg.continuity.messages/1",
        "messages": messages,
        "meta": {
            "surface": "grok",
            "provenance": "raw",
            "messages_sha256": seal_messages_sha256(messages),
            "turn_count": max(int(m.get("turn_index") or 1) for m in messages),
            "opened_at": _START,
        },
    }


def _step(env: dict, uri: str) -> dict:
    return {
        "snapshot_uri": uri,
        "envelope": env,
        "messages_sha256": env["meta"]["messages_sha256"],
        "surface": "grok",
        "turn_count": env["meta"]["turn_count"],
        "coverage": "full",
        "provenance": "raw",
        "producer": "contract",
        "sha256": "staging",
    }


def _options(agent_id: str = "agent-contract-1") -> dict:
    return {
        "bus_identity": "grok-bot-hm1",
        "agent_id": agent_id,
        "house": "hm1",
        "n": 1,
        "root_thread": "15903",
    }


class _Spy:
    def __init__(self, *, lookup_raises: bool = False) -> None:
        self.events: list[dict] = []
        self.closes = 0
        self.lookup_raises = lookup_raises
        self.close_sig = inspect.signature(_op_session_close)
        self.entity_sig = inspect.signature(_op_entity_get)

    async def dispatch(self, tool: str, args: dict) -> dict:
        if tool == "entity_get":
            self.entity_sig.bind(**args)
            if self.lookup_raises:
                raise RuntimeError("lookup down")
            return {}
        if tool != "session_close":
            raise AssertionError(tool)
        self.close_sig.bind(**args)
        self.closes += 1
        result = _op_session_close(**args)
        return result if isinstance(result, dict) else {"error": "non_object"}


def _journal_rows(agent_id: str) -> list:
    import cortex_store.db as cortex_db

    conn = cortex_db.cortex_conn()
    try:
        return list(
            conn.execute(
                "SELECT id, session_id, conversation_uuid, verbatim_codec "
                "FROM session_journals WHERE conversation_uuid = ?",
                (agent_id,),
            ).fetchall()
        )
    finally:
        conn.close()


def _opened(session_id: str) -> str | None:
    import cortex_store.db as cortex_db

    conn = cortex_db.cortex_conn()
    try:
        row = conn.execute(
            "SELECT attributes FROM entities WHERE id = ?",
            (f"transcript:{session_id}",),
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["attributes"]:
        return None
    return json.loads(row["attributes"]).get("opened_at")


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Spy:
    _boot(monkeypatch, tmp_path)
    made = _Spy()
    monkeypatch.setattr(
        "pipelines.grokbot_checkpoint.v1.handlers.seal.stargate_continuity_checkpoint_sealed",
        lambda **kw: made.events.append(kw),
    )
    return made


def _patch_event(monkeypatch, spy: _Spy) -> None:
    from . import seal as seal_mod

    monkeypatch.setattr(
        seal_mod,
        "stargate_continuity_checkpoint_sealed",
        lambda **kw: spy.events.append(kw),
    )


@pytest.mark.asyncio
async def test_1_fresh_seal(monkeypatch, tmp_path: Path) -> None:
    _boot(monkeypatch, tmp_path)
    spy = _Spy()
    _patch_event(monkeypatch, spy)
    env = _envelope(_messages(1))
    out = await run_seal(
        _options(), _step(env, _URI),
        execution_id="e", dispatch=spy.dispatch,
    )
    assert out["refused"] is None
    rows = _journal_rows("agent-contract-1")
    assert len(rows) == 1
    assert rows[0]["verbatim_codec"] == "messages-v1"
    assert _opened(rows[0]["session_id"]) == _START
    assert len(spy.events) == 1


@pytest.mark.asyncio
async def test_2_rerun_same_row(monkeypatch, tmp_path: Path) -> None:
    _boot(monkeypatch, tmp_path)
    spy = _Spy()
    _patch_event(monkeypatch, spy)
    env = _envelope(_messages(1))
    step = _step(env, _URI)
    first = await run_seal(_options(), step, execution_id="e", dispatch=spy.dispatch)
    second = await run_seal(_options(), step, execution_id="e", dispatch=spy.dispatch)
    assert second["session_id"] == first["session_id"]
    assert second["journal_row_id"] == first["journal_row_id"]
    assert second["already_closed"] is False
    assert len(_journal_rows("agent-contract-1")) == 1
    assert len(spy.events) == 1


@pytest.mark.asyncio
async def test_3_prefix_extend(monkeypatch, tmp_path: Path) -> None:
    _boot(monkeypatch, tmp_path)
    spy = _Spy()
    _patch_event(monkeypatch, spy)
    uri = _URI
    first_env = _envelope(_messages(1))
    first = await run_seal(
        _options(), _step(first_env, uri), execution_id="e", dispatch=spy.dispatch
    )
    second_env = _envelope(_messages(2))
    second = await run_seal(
        _options(), _step(second_env, uri), execution_id="e", dispatch=spy.dispatch
    )
    assert second["refused"] is None
    assert second["session_id"] == first["session_id"]
    assert second["messages_sha256"] != first["messages_sha256"]
    assert len(spy.events) == 2


@pytest.mark.asyncio
async def test_4_diverged_refuses(monkeypatch, tmp_path: Path) -> None:
    _boot(monkeypatch, tmp_path)
    spy = _Spy()
    _patch_event(monkeypatch, spy)
    uri = _URI
    await run_seal(
        _options(), _step(_envelope(_messages(1)), uri), execution_id="e", dispatch=spy.dispatch
    )
    card = tmp_path / "card.md"
    card.write_text("untouched", encoding="utf-8")
    before = card.read_bytes()
    out = await run_seal(
        _options(),
        _step(_envelope(_messages(2, diverged=True)), uri),
        execution_id="e",
        dispatch=spy.dispatch,
    )
    assert out["refused"]["code"] == "succession.verbatim_diverged"
    assert card.read_bytes() == before
    assert len(spy.events) == 1


@pytest.mark.asyncio
async def test_5_short_turns_clear_minimum(monkeypatch, tmp_path: Path) -> None:
    _boot(monkeypatch, tmp_path)
    spy = _Spy()
    _patch_event(monkeypatch, spy)
    short = [
        {"role": "user", "content": "hi", "turn_index": 1, "timestamp": _START},
        {"role": "assistant", "content": "yo", "turn_index": 1},
    ]
    out = await run_seal(
        _options(),
        _step(_envelope(short), _URI),
        execution_id="e",
        dispatch=spy.dispatch,
    )
    assert out["refused"] is None
    assert out["session_id"]


@pytest.mark.asyncio
async def test_6_threads_path_refused(monkeypatch, tmp_path: Path) -> None:
    _boot(monkeypatch, tmp_path)
    from continuity_tape.seal_messages import seal_messages_envelope

    called: list[str] = []

    async def dispatch(tool: str, args: dict) -> dict:
        called.append(tool)
        return {}

    result = await seal_messages_envelope(
        _envelope(_messages(1)),
        agent="grok-bot-hm1",
        seat_slug="grok-bot",
        conversation_uuid="agent-contract-1",
        opened_at=_START,
        thread="15903",
        structural_md="## Session Summary\n\n" + ("x" * 180),
        summary="short summary text here!!",
        cortex_dispatch_fn=dispatch,
        transcript_messages_path="notes/system/threads/x.messages.json",
    )
    assert result.refused
    assert called == []
    assert _journal_rows("agent-contract-1") == []


@pytest.mark.asyncio
async def test_7_default_staging_slug(fs) -> None:
    source = {
        "envelope": _envelope(_messages(1)),
        "messages_sha256": _envelope(_messages(1))["meta"]["messages_sha256"],
        "provenance": "raw",
        "producer": "contract",
        "turn_count": 1,
        "surface": "grok",
    }
    house = "a/b ..c d"
    out = await run_envelope({"house": house, "agent_id": "abcdefghij"}, source, fs)
    assert out["refused"] is None
    assert out["snapshot_uri"] == staging_uri(house, "abcdefghij", "raw")
    assert out["snapshot_uri"].startswith("ephemeral/harvests/grok/")
    assert "/" not in out["snapshot_uri"].split("ephemeral/harvests/grok/", 1)[1].split("-")[0]
    assert " " not in out["snapshot_uri"]
    assert ".." not in out["snapshot_uri"]


@pytest.mark.asyncio
async def test_8_agent_and_id_prefix(monkeypatch, tmp_path: Path) -> None:
    from agent_seat.session_id import SESSION_ID_RE

    _boot(monkeypatch, tmp_path)
    spy = _Spy()
    _patch_event(monkeypatch, spy)
    out = await run_seal(
        _options(),
        _step(_envelope(_messages(1)), _URI),
        execution_id="e",
        dispatch=spy.dispatch,
    )
    assert out["close_args"]["agent"] == "grok-bot-hm1"
    assert out["session_id"].startswith("grok-bot-")
    assert SESSION_ID_RE.fullmatch(out["session_id"])


@pytest.mark.asyncio
async def test_9_lookup_raise_same_id(monkeypatch, tmp_path: Path) -> None:
    _boot(monkeypatch, tmp_path)
    happy = _Spy()
    _patch_event(monkeypatch, happy)
    step = _step(_envelope(_messages(1)), _URI)
    first = await run_seal(_options("agent-lookup"), step, execution_id="e", dispatch=happy.dispatch)
    boom = _Spy(lookup_raises=True)
    second = await run_seal(
        _options("agent-lookup"), step, execution_id="e", dispatch=boom.dispatch
    )
    assert second["session_id"] == first["session_id"]


def test_10_missing_dispatch_thread(monkeypatch, tmp_path: Path) -> None:
    from .admit import GrokbotCheckpointAdmitHandler

    class _Ctx:
        dispatch_thread_id = ""
        options: dict = {}
        outputs: dict = {}

    import asyncio

    handler = GrokbotCheckpointAdmitHandler()
    out = asyncio.run(handler.execute(None, _Ctx()))
    assert out.json["refused"]["code"] == "admit.dispatch_thread_missing"


def test_input_extraction_reads_top_level_thread() -> None:
    class _Ctx:
        original_request = {"dispatch_thread_id": "15903", "messages": []}

    class _Empty:
        original_request = {"messages": []}

    assert extract_dispatch_thread_id(_Ctx()) == "15903"
    assert extract_dispatch_thread_id(_Empty()) is None


def test_conversation_uuid_on_insert_and_reclose(monkeypatch, tmp_path: Path) -> None:
    _boot(monkeypatch, tmp_path)
    spy = _Spy()
    _patch_event(monkeypatch, spy)
    step = _step(_envelope(_messages(1)), _URI)

    async def _go():
        await run_seal(_options("agent-uuid"), step, execution_id="e", dispatch=spy.dispatch)
        rows = _journal_rows("agent-uuid")
        assert rows and rows[0]["conversation_uuid"] == "agent-uuid"
        await run_seal(_options("agent-uuid"), step, execution_id="e", dispatch=spy.dispatch)
        again = _journal_rows("agent-uuid")
        assert len(again) == 1
        assert again[0]["conversation_uuid"] == "agent-uuid"
        assert again[0]["id"] == rows[0]["id"]

    import asyncio

    asyncio.run(_go())


def test_roots_disagree(monkeypatch, tmp_path: Path) -> None:
    from .envelope import files_roots_agree

    monkeypatch.setattr(
        "cortex_store.dispatch_ops._shared._FILES_ROOT", tmp_path / "other"
    )
    assert files_roots_agree() is False
