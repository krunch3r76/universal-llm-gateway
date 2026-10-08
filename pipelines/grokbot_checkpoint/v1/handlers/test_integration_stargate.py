"""Real Stargate registry, concurrency key, and DAG executor.

Fakes only cortex_dispatch (session_close), the agent-bus client, and the
succession stamp. File IO uses RootFs under CORTEX_FILES_ROOT.
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


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch):
    import inspect

    from continuity_tape.events import stargate_continuity_checkpoint_sealed
    from cortex_store.dispatch_ops.ops_transcript_seal import _stamp_succession_fields

    calls: list[str] = []
    close_args: list[dict] = []
    seen_ids: set[str] = set()
    posts: list[dict] = []
    turns: list[dict] = []
    stamp_sig = inspect.signature(_stamp_succession_fields)
    event_sig = inspect.signature(stargate_continuity_checkpoint_sealed)

    async def dispatch(tool: str, args: dict) -> dict:
        assert tool in _OP_SPECS
        calls.append(tool)
        if tool != "session_close":
            return {}
        close_args.append(dict(args))
        sid = str(args["session_id"])
        already = sid in seen_ids
        seen_ids.add(sid)
        return {"session_id": sid, "already_closed": already, "reason": None}

    def stamp(**kwargs):
        stamp_sig.bind(**kwargs)

    def event(**kwargs):
        event_sig.bind(**kwargs)

    async def get(path: str, params=None):
        return {"turns": list(turns)}, 200

    async def send(**kwargs):
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
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_transcript_seal._stamp_succession_fields",
        stamp,
    )
    for mod in (admit, post, fact, await_verify):
        monkeypatch.setattr(mod, "bus_get", get)
        if hasattr(mod, "bus_send"):
            monkeypatch.setattr(mod, "bus_send", send)
    return {"calls": calls, "posts": posts, "turns": turns, "close_args": close_args}


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
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "ws"))
    import cortex_store.db as cortex_db
    from cortex_store._test_db_bootstrap import materialize_head_schema_template

    db_path = tmp_path / "cortex.db"
    materialize_head_schema_template(db_path)
    monkeypatch.setattr(cortex_db, "_CORTEX_DB", db_path)
    cortex_db.run_migrations(cortex_db.cortex_conn())
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
    from cortex_store.dispatch_ops import _shared
    from cortex_store.models import SessionCloseRequest
    from cortex_store.routes.session_close_validate import (
        _resolve_transcript_messages_envelope,
    )

    monkeypatch.setattr(_shared, "_FILES_ROOT", tmp_path)
    resolved = _resolve_transcript_messages_envelope(
        SessionCloseRequest(
            session_id=happy["seal"]["session_id"],
            agent="grok-bot",
            summary="succession seal (grokbot-checkpoint-v1)",
            session_summary_md="succession seal (grokbot-checkpoint-v1)",
            transcript_messages_path=snap_rel,
            transcript_depth="verbatim",
            closed_by="succession",
            succession_seal_authority=True,
        )
    )
    assert resolved.meta.provenance == "transcribed"
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
    rerun_card = _CARD.encode()
    (tmp_path / "notes" / "rerun.md").write_bytes(rerun_card)
    first = await _run(
        registry,
        {
            **base,
            "mode": "seal",
            "house": "rerun",
            "agent_id": "grok-rerun",
            "n": 3,
            "card_uri": "notes/rerun.md",
            "expected_card_sha256": "ab" * 32,
            "snapshot_uri": "notes/system/transcripts/grok/rerun.raw.messages.json",
            "raw_ref": "notes/raw.json",
            "raw_sha256": file_sha256(raw),
        },
        tmp_path,
    )
    assert first["seal"].get("session_id")
    assert first["card"].get("refused")
    seeded = "grok-bot-2026-01-01-000000-abc"
    conn = cortex_db.cortex_conn()
    conn.execute(
        "INSERT INTO session_journals (session_id, conversation_uuid, closed_by, "
        "file_path, agent, summary, timestamp) VALUES (?, ?, 'succession', ?, ?, ?, ?)",
        (seeded, "grok-rerun", "notes/x", "grok-bot", "seeded row", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()
    calls_at_rerun = len(transport["calls"])
    second = await _run(
        registry,
        {
            **base,
            "mode": "seal",
            "house": "rerun",
            "agent_id": "grok-rerun",
            "n": 3,
            "card_uri": "notes/rerun.md",
            "expected_card_sha256": file_sha256(rerun_card),
            "snapshot_uri": "notes/system/transcripts/grok/rerun.raw.messages.json",
            "raw_ref": "notes/raw.json",
            "raw_sha256": file_sha256(raw),
        },
        tmp_path,
    )
    assert second["seal"]["session_id"] == seeded
    assert second["seal"]["session_id"] != first["seal"]["session_id"]
    from cortex_store.models import SessionCloseRequest
    from cortex_store.routes.session_close_persist import try_idempotent_session_close
    from cortex_store.routes.session_close_validate import ValidatedCloseContext

    response, row_id = try_idempotent_session_close(
        SessionCloseRequest(
            session_id=seeded,
            agent="grok-bot",
            summary="succession seal (grokbot-checkpoint-v1)",
            session_summary_md="succession seal (grokbot-checkpoint-v1)",
            transcript_messages_path="notes/system/transcripts/grok/rerun.raw.messages.json",
            transcript_depth="verbatim",
            closed_by="succession",
            succession_seal_authority=True,
        ),
        ValidatedCloseContext(
            composed_md=None,
            turn_count=0,
            heading_warning=None,
            transcript_entity_id=None,
            transcript_path=None,
            source_uri=None,
            now="2026-01-01T00:00:00Z",
            opened_at=None,
        ),
    )
    assert response is None
    assert row_id is not None
    assert len(transport["calls"]) == calls_at_rerun + 1

    assert light["card"].get("written_sha256")
    assert light["seal"].get("_skipped") is True
