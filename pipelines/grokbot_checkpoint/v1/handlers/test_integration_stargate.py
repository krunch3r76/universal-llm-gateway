"""Real Stargate registry, concurrency key, and DAG executor.

The seal step calls the real in-process session_close handler (validate,
persist, dedupe, succession stamp). Fakes are the HTTP transport hop and the
bus (agent-bus client and the sealed-event publisher).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from cortex_store.dispatch_ops import _OP_SPECS
from systems.pipeline.core.dag import DAGBuilder
from systems.pipeline.core.domain_router import get_domain_router
from systems.pipeline.core.execution.concurrency import _render_key
from systems.pipeline.core.execution.dag_executor import DAGExecutor
from systems.pipeline.core.handlers.pipeline_context import PipelineContext
from systems.pipeline.registry.core import PipelineRegistry

from . import register_handlers
from ._clients import file_sha256

pytestmark = pytest.mark.offline

_PIPE = Path(__file__).resolve().parents[1] / "grokbot-checkpoint-v1.yaml"
_CATS = Path(__file__).resolve().parents[3] / "categories.yaml"
_FIXTURES = Path(__file__).parent / "fixtures"
_CARD = """# house

## Checkpoint log

| n | when | trigger | expected | envelope | pipeline |
|---|---|---|---|---|---|
"""


def _load_registry(tmp: Path) -> PipelineRegistry:
    root = tmp / "search"
    dest = root / "grokbot_checkpoint" / "v1"
    dest.mkdir(parents=True)
    shutil.copy(_CATS, root / "categories.yaml")
    shutil.copy(_PIPE, dest / "grokbot-checkpoint-v1.yaml")
    register_handlers(get_domain_router())
    registry = PipelineRegistry(search_paths=[str(root)], config_base_dir=root)
    registry.load()
    return registry


def _raw_bytes() -> bytes:
    env = json.loads((_FIXTURES / "grok_transcribed_pour.json").read_text())
    env["meta"]["provenance"] = "raw"
    env["meta"]["source_sha256"] = "ab" * 32
    return json.dumps(env).encode()


def bind_files_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Point every session_close binding of the files root at this temp tree."""
    # ops_journals first: importing ops_session_close before it cycles through routes.
    from cortex_store.dispatch_ops import ops_journals  # isort: skip

    import cortex_store.dispatch_ops._shared as shared
    import cortex_store.dispatch_ops.ops_session_close as ops_session_close
    import cortex_store.routes.session_close_persist as session_close_persist
    import cortex_store.routes.session_journals as session_journals

    assert ops_journals._op_session_close is ops_session_close._op_session_close

    for module in (shared, ops_session_close, session_close_persist, session_journals):
        monkeypatch.setattr(module, "_FILES_ROOT", root)


def boot_cortex(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import cortex_store.db as cortex_db
    from cortex_store._test_db_bootstrap import materialize_head_schema_template

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "ws"))
    bind_files_root(monkeypatch, tmp_path)
    db_path = tmp_path / "cortex.db"
    materialize_head_schema_template(db_path)
    monkeypatch.setattr(cortex_db, "_CORTEX_DB", db_path)
    cortex_db.run_migrations(cortex_db.cortex_conn())


def journal_count(agent_id: str) -> int:
    import cortex_store.db as cortex_db

    conn = cortex_db.cortex_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM session_journals WHERE conversation_uuid = ?",
            (agent_id,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"])


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch):
    import inspect

    from continuity_tape.events import stargate_continuity_checkpoint_sealed
    from cortex_store.dispatch_ops import execute_op

    from ._clients import bus_get, bus_send

    calls: list[str] = []
    close_args: list[dict] = []
    posts: list[dict] = []
    turns: list[dict] = []
    events: list[dict] = []
    event_sig = inspect.signature(stargate_continuity_checkpoint_sealed)
    get_sig = inspect.signature(bus_get)
    send_sig = inspect.signature(bus_send)
    dispatch_sig = inspect.signature(execute_op)

    async def dispatch(tool: str, args: dict) -> dict:
        assert tool in _OP_SPECS
        dispatch_sig.bind(tool, args)
        calls.append(tool)
        if tool != "session_close":
            return {}
        close_args.append(dict(args))
        result = execute_op(tool, args)
        return result if isinstance(result, dict) else {"error": "non_object_response"}

    def event(**kwargs):
        event_sig.bind(**kwargs)
        events.append(kwargs)

    async def get(path: str, params=None):
        get_sig.bind(path, params=params)
        return {"turns": list(turns)}, 200

    async def send(**kwargs):
        send_sig.bind(**kwargs)
        posts.append(kwargs)
        turns.append(
            {
                "subject": kwargs.get("subject"),
                "body": kwargs.get("body"),
                "turn_number": len(turns) + 1,
                "superseded": False,
                "from": kwargs.get("from_agent"),
            }
        )
        return {"turn_number": len(turns)}, 200

    from . import admit, await_verify, fact, post, seal

    monkeypatch.setattr(seal, "cortex_dispatch", dispatch)
    monkeypatch.setattr(seal, "stargate_continuity_checkpoint_sealed", event)
    for mod in (admit, post, fact, await_verify):
        monkeypatch.setattr(mod, "bus_get", get)
        if hasattr(mod, "bus_send"):
            monkeypatch.setattr(mod, "bus_send", send)
    return {
        "calls": calls,
        "posts": posts,
        "turns": turns,
        "close_args": close_args,
        "events": events,
        "dispatch": dispatch,
    }


async def _run(registry: PipelineRegistry, options: dict, root: Path) -> dict:
    spec = registry.pipelines["grokbot-checkpoint-v1"]
    nodes = DAGBuilder(spec.steps).build()
    ctx = PipelineContext(
        pipeline=spec,
        source_text="",
        http_request=None,  # type: ignore[arg-type]
        execution_id="exec-int",
        runtime_options=options,
        dispatch_thread_id="15902",
        _registry=registry,
    )
    await DAGExecutor(nodes, ctx).execute()
    out = {name: item.json for name, item in ctx.outputs.items()}
    out["_files"] = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    return out


def test_registry_keeps_pipeline_and_renders_key(tmp_path: Path) -> None:
    registry = _load_registry(tmp_path)
    assert "grokbot-checkpoint-v1" in registry.pipelines
    removed = registry._validator._remove_invalid_pipelines()
    assert "grokbot-checkpoint-v1" not in removed
    assert "grokbot-checkpoint-v1" in registry.pipelines
    spec = registry.pipelines["grokbot-checkpoint-v1"]
    ctx = PipelineContext(
        pipeline=spec,
        source_text="",
        http_request=None,  # type: ignore[arg-type]
        dispatch_thread_id="15902",
    )
    rendered = _render_key(spec, ctx, spec.model_extra["concurrency"]["key"])
    assert rendered == "grokbot-cp:15902"


@pytest.mark.asyncio
async def test_dag_happy_and_refusals(tmp_path: Path, monkeypatch, transport) -> None:
    boot_cortex(monkeypatch, tmp_path)
    registry = _load_registry(tmp_path / "reg")
    card = _CARD.encode()
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "card.md").write_bytes(card)
    raw = _raw_bytes()
    transcribed = (_FIXTURES / "grok_transcribed_pour.json").read_bytes()
    (tmp_path / "notes" / "raw.json").write_bytes(raw)
    (tmp_path / "notes" / "tr.json").write_bytes(transcribed)
    base = {
        "house": "h",
        "n": 1,
        "agent_id": "agent-12345678",
        "bus_identity": "grok-bot",
        "root_thread": "10",
        "verify_lane": "11",
        "verifier": "verifier",
        "trigger": "hop",
        "card_uri": "notes/card.md",
        "expected_card_sha256": file_sha256(card),
        "snapshot_uri": "notes/system/transcripts/grok/h-agent123.transcribed.messages.json",
        "channel": "cp",
        "residue": "r",
        "wait_verify_seconds": 0,
    }
    happy = await _run(
        registry,
        {
            **base,
            "mode": "seal",
            "transcribed_ref": "notes/tr.json",
            "transcribed_sha256": file_sha256(transcribed),
            "raw_ref": "notes/raw.json",
            "raw_sha256": file_sha256(raw),
        },
        tmp_path,
    )
    assert happy["source"].get("provenance") == "transcribed"
    assert happy["source"].get("alternate")
    assert happy["seal"].get("session_id")
    assert "session_close" in transport["calls"]
    assert transport["posts"]
    assert "verify_resume" not in happy["result"]["completed_steps"]
    snap_rel = base["snapshot_uri"]
    snap_bytes = (tmp_path / snap_rel).read_bytes()
    assert b'"provenance": "transcribed"' in snap_bytes or b'"provenance":"transcribed"' in snap_bytes
    from cortex_store.models import SessionCloseRequest
    from cortex_store.routes.session_close_validate import (
        _resolve_transcript_messages_envelope,
    )
    from cortex_store.session_close_validation import _validate_session_close_args

    sent = transport["close_args"][0]
    resolved = _resolve_transcript_messages_envelope(
        SessionCloseRequest(
            session_id=sent["session_id"],
            agent=sent["agent"],
            summary=sent["summary"],
            session_summary_md=sent["session_summary_md"],
            transcript_messages_path=sent["transcript_messages_path"],
            transcript_depth=sent["transcript_depth"],
            closed_by=sent["closed_by"],
            succession_seal_authority=sent["succession_seal_authority"],
            entity_ids=sent["entity_ids"],
        )
    )
    assert resolved.meta.provenance == "transcribed"
    assert _validate_session_close_args(
        session_id=sent["session_id"],
        agent=sent["agent"],
        transcript_jsonl_path=None,
        session_summary_md=sent["session_summary_md"],
        summary=sent["summary"],
        transcript_messages_path=sent["transcript_messages_path"],
        transcript_depth=sent["transcript_depth"],
        emit_rejected=False,
    ) is None
    assert sent["agent"] == "grok-bot"
    assert sent["transcript_messages_path"] == snap_rel
    from agent_seat.session_id import SESSION_ID_RE

    assert SESSION_ID_RE.fullmatch(happy["seal"]["session_id"])
    assert transport["close_args"][0]["transcript_messages_path"].startswith(
        "notes/system/transcripts/"
    )

    tr_card = _CARD.encode()
    (tmp_path / "notes" / "tronly.md").write_bytes(tr_card)
    tr_only = await _run(
        registry,
        {
            **base,
            "mode": "seal",
            "house": "tronly",
            "agent_id": "grok-tronly",
            "card_uri": "notes/tronly.md",
            "expected_card_sha256": file_sha256(tr_card),
            "snapshot_uri": "notes/system/transcripts/grok/tronly.transcribed.messages.json",
            "transcribed_ref": "notes/tr.json",
            "transcribed_sha256": file_sha256(transcribed),
        },
        tmp_path,
    )
    assert tr_only["source"]["provenance"] == "transcribed"
    assert tr_only["source"].get("alternate") is None
    assert tr_only["source"].get("fallback") is None
    assert tr_only["result"]["refused"] is None

    raw_card = _CARD.encode()
    (tmp_path / "notes" / "rawonly.md").write_bytes(raw_card)
    raw_only = await _run(
        registry,
        {
            **base,
            "mode": "seal",
            "house": "rawonly",
            "agent_id": "grok-rawonly",
            "card_uri": "notes/rawonly.md",
            "expected_card_sha256": file_sha256(raw_card),
            "snapshot_uri": "notes/system/transcripts/grok/rawonly.raw.messages.json",
            "raw_ref": "notes/raw.json",
            "raw_sha256": file_sha256(raw),
        },
        tmp_path,
    )
    raw_snap = (tmp_path / "notes/system/transcripts/grok/rawonly.raw.messages.json").read_bytes()
    assert b'"provenance": "raw"' in raw_snap or b'"provenance":"raw"' in raw_snap
    assert "provenance:raw" in (tmp_path / "notes" / "rawonly.md").read_text()
    assert raw_only["source"].get("provenance") == "raw"
    assert raw_only["source"].get("alternate") is None
    assert raw_only["source"].get("fallback") is None
    assert raw_only["result"]["refused"] is None

    calls_before = len(transport["calls"])
    posts_before = len(transport["posts"])
    bad = json.loads(raw)
    bad["meta"]["capture"] = {
        "kind": "jsonl",
        "ref": "cortex://notes/cap.bin",
        "sha256": "ab" * 32,
        "pseudo_byte_exact": False,
    }
    bad["meta"]["source_sha256"] = "ab" * 32
    blob = json.dumps(bad).encode()
    bad_card = _CARD.encode()
    (tmp_path / "notes" / "bad.md").write_bytes(bad_card)
    (tmp_path / "notes" / "bad.json").write_bytes(blob)
    (tmp_path / "notes" / "cap.bin").write_bytes(b"nope")
    mismatch = await _run(
        registry,
        {
            **base,
            "mode": "seal",
            "house": "bad",
            "agent_id": "grok-bad",
            "raw_ref": "notes/bad.json",
            "raw_sha256": file_sha256(blob),
            "card_uri": "notes/bad.md",
            "expected_card_sha256": file_sha256(bad_card),
            "snapshot_uri": "notes/system/transcripts/grok/bad.raw.messages.json",
        },
        tmp_path,
    )
    assert mismatch["source"]["refused"]["code"] == "capture.sha_mismatch"
    assert mismatch["seal"].get("_skipped") is True
    assert len(transport["calls"]) == calls_before
    assert len(transport["posts"]) == posts_before
    assert file_sha256((tmp_path / "notes" / "bad.md").read_bytes()) == file_sha256(bad_card)
    assert not (tmp_path / "notes/system/transcripts/grok/bad.raw.messages.json").exists()

    refused = await _run(
        registry,
        {**base, "mode": "seal", "raw_ref": "../x", "raw_sha256": "ab" * 32},
        tmp_path,
    )
    assert refused["admit"]["refused"]["code"] == "admit.path_escape"
    assert refused["card"].get("_skipped") is True
    assert len(transport["calls"]) == calls_before

    (tmp_path / "notes" / "card.md").write_bytes(
        (
            _CARD
            + "| 1 | t | hop | sha | cell | pipeline e |\n"
        ).encode()
    )
    transport["turns"].append(
        {"subject": "FACT|house-cp|h|1", "superseded": False, "body": "x", "from": "a"}
    )
    replay = await _run(
        registry,
        {
            **base,
            "mode": "seal",
            "expected_card_sha256": file_sha256((tmp_path / "notes" / "card.md").read_bytes()),
            "raw_ref": "notes/raw.json",
            "raw_sha256": file_sha256(raw),
        },
        tmp_path,
    )
    assert replay["admit"].get("replayed") is True
    assert replay["fact"].get("_skipped") is True
    assert len(transport["posts"]) == posts_before

    light_card = _CARD.encode()
    (tmp_path / "notes" / "light.md").write_bytes(light_card)
    light = await _run(
        registry,
        {
            **base,
            "mode": "light",
            "house": "light",
            "n": 1,
            "card_uri": "notes/light.md",
            "expected_card_sha256": file_sha256(light_card),
        },
        tmp_path,
    )
    assert light["card"].get("written_sha256")
    assert light["seal"].get("_skipped") is True


def _seal_options(tmp_path: Path, *, house: str, agent_id: str, n: int, raw: bytes) -> dict:
    card = _CARD.encode()
    card_name = f"notes/{house}-card.md"
    (tmp_path / "notes").mkdir(exist_ok=True)
    (tmp_path / card_name).write_bytes(card)
    raw_name = f"notes/{house}-raw.json"
    (tmp_path / raw_name).write_bytes(raw)
    return {
        "house": house,
        "n": n,
        "agent_id": agent_id,
        "bus_identity": "grok-bot-hm1",
        "root_thread": "10",
        "verify_lane": "11",
        "verifier": "verifier",
        "trigger": "hop",
        "card_uri": card_name,
        "expected_card_sha256": file_sha256(card),
        "snapshot_uri": (
            f"notes/system/transcripts/grok/{house}-{agent_id[:8]}.raw.messages.json"
        ),
        "channel": "cp",
        "residue": "r",
        "wait_verify_seconds": 0,
        "mode": "seal",
        "raw_ref": raw_name,
        "raw_sha256": file_sha256(raw),
    }


def _sealed_on(agent_id: str) -> str | None:
    import cortex_store.db as cortex_db

    conn = cortex_db.cortex_conn()
    try:
        row = conn.execute(
            "SELECT sealed_on FROM session_journals WHERE conversation_uuid = ?",
            (agent_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["sealed_on"] is None:
        return None
    return str(row["sealed_on"])


@pytest.mark.asyncio
async def test_rerun_is_idempotent_until_snapshot_grows(
    tmp_path: Path, monkeypatch, transport
) -> None:
    from datetime import UTC, datetime

    from . import seal as seal_mod

    boot_cortex(monkeypatch, tmp_path)
    registry = _load_registry(tmp_path / "reg")
    raw = _raw_bytes()
    agent_id = "agent-hm1idem"
    options = _seal_options(tmp_path, house="idem", agent_id=agent_id, n=1, raw=raw)
    stamps = iter(
        [
            datetime(2026, 3, 1, 0, 0, 1, tzinfo=UTC),
            datetime(2026, 3, 1, 0, 0, 2, tzinfo=UTC),
            datetime(2026, 3, 1, 0, 0, 3, tzinfo=UTC),
        ]
    )

    class _Clock:
        @staticmethod
        def now(tz=None):
            return next(stamps)

    monkeypatch.setattr(seal_mod, "datetime", _Clock)
    first = await _run(registry, options, tmp_path)
    assert first["seal"].get("refused") is None
    assert first["seal"]["already_closed"] is False
    assert journal_count(agent_id) == 1
    sid = first["seal"]["session_id"]
    sealed = _sealed_on(agent_id)
    assert sealed == "2026-03-01T00:00:01Z"
    assert len(transport["events"]) == 1
    assert len(transport["calls"]) == 1

    second = await _run(
        registry,
        _seal_options(tmp_path, house="idem", agent_id=agent_id, n=2, raw=raw),
        tmp_path,
    )
    assert second["seal"]["already_closed"] is True
    assert second["seal"]["session_id"] == sid
    assert journal_count(agent_id) == 1
    assert _sealed_on(agent_id) == sealed
    assert len(transport["events"]) == 1
    assert len(transport["calls"]) == 1

    from continuity_tape.messages import seal_messages_sha256

    grown = json.loads(raw)
    grown["messages"].append(
        {"role": "user", "content": "one more turn", "turn_index": 2}
    )
    grown["meta"]["messages_sha256"] = seal_messages_sha256(grown["messages"])
    grown["meta"]["message_count"] = len(grown["messages"])
    grown["meta"]["turn_count"] = 2
    grown_bytes = json.dumps(grown).encode()
    grown_options = _seal_options(
        tmp_path, house="idem", agent_id=agent_id, n=3, raw=grown_bytes
    )
    third = await _run(registry, grown_options, tmp_path)
    assert third["seal"].get("refused") is None
    assert third["seal"]["already_closed"] is False
    assert third["seal"]["session_id"] == sid
    assert journal_count(agent_id) == 1
    assert _sealed_on(agent_id) == "2026-03-01T00:00:03Z"
    assert len(transport["events"]) == 2
    assert len(transport["calls"]) == 2


@pytest.mark.asyncio
async def test_crash_before_session_close_reseals(tmp_path: Path, monkeypatch, transport) -> None:
    from . import seal as seal_mod

    boot_cortex(monkeypatch, tmp_path)
    registry = _load_registry(tmp_path / "reg")
    raw = _raw_bytes()
    agent_id = "agent-hm1crash"
    options = _seal_options(tmp_path, house="crash", agent_id=agent_id, n=1, raw=raw)

    async def die(tool: str, args: dict) -> dict:
        raise RuntimeError("died before session_close")

    monkeypatch.setattr(seal_mod, "cortex_dispatch", die)
    from systems.pipeline.core.dag import PipelineExecutionError

    with pytest.raises(PipelineExecutionError, match="died before session_close"):
        await _run(registry, options, tmp_path)
    assert journal_count(agent_id) == 0
    snap = tmp_path / options["snapshot_uri"]
    assert snap.is_file()
    monkeypatch.setattr(seal_mod, "cortex_dispatch", transport["dispatch"])
    calls_before = len(transport["calls"])
    second = await _run(registry, options, tmp_path)
    assert second["seal"].get("refused") is None
    assert second["seal"]["already_closed"] is False
    assert journal_count(agent_id) == 1
    assert len(transport["calls"]) == calls_before + 1


@pytest.mark.asyncio
async def test_legacy_threads_path_refused(tmp_path: Path, monkeypatch, transport) -> None:
    boot_cortex(monkeypatch, tmp_path)
    registry = _load_registry(tmp_path / "reg")
    raw = _raw_bytes()
    agent_id = "agent-hm1old"
    options = _seal_options(tmp_path, house="oldp", agent_id=agent_id, n=1, raw=raw)
    options["snapshot_uri"] = "notes/system/threads/oldp.messages.json"
    card_before = (tmp_path / options["card_uri"]).read_bytes()
    out = await _run(registry, options, tmp_path)
    refused = out["seal"]["refused"]
    assert "transcript_messages_path.invalid" in refused["message"]
    assert journal_count(agent_id) == 0
    assert transport["events"] == []
    assert _sealed_on(agent_id) is None
    snap_after = (tmp_path / options["snapshot_uri"]).read_bytes()
    assert (tmp_path / options["card_uri"]).read_bytes() == card_before
    assert snap_after.startswith(b"{") or snap_after.startswith(b"[")
