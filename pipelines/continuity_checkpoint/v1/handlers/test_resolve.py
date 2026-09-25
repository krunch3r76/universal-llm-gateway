"""Unit tests for ContinuityCheckpointResolveHandler."""

from __future__ import annotations

import pytest

from .resolve import ContinuityCheckpointResolveHandler

pytestmark = pytest.mark.offline


class _Ctx:
    execution_id = "exec-001"
    dispatch_thread_id = "10223"
    options = {
        "thread": "10223",
        "surface": "cursor",
        "from_agent": "cursor",
        "jsonl_path": "abc/uuid/file.jsonl",
    }


class _Step:
    pass


@pytest.mark.asyncio
async def test_cursor_jsonl_path_passthrough() -> None:
    handler = ContinuityCheckpointResolveHandler()
    out = await handler.execute(_Step(), _Ctx())
    assert out.json["jsonl_path"] == "abc/uuid/file.jsonl"


@pytest.mark.asyncio
async def test_claude_ai_resolve_happy_path() -> None:
    ctx = _Ctx()
    ctx.options = {
        "thread": "10223",
        "surface": "claude_ai",
        "from_agent": "cursor",
        "chat_url": "https://claude.ai/cowork/cse_0181xcjbYP83D8VdBopSyiLs",
    }
    handler = ContinuityCheckpointResolveHandler()
    out = await handler.execute(_Step(), ctx)
    assert out.json["transcript_id"] == "cse_0181xcjbYP83D8VdBopSyiLs"
    assert out.json["chat_url"].endswith("cse_0181xcjbYP83D8VdBopSyiLs")


@pytest.mark.asyncio
async def test_claude_ai_resolve_missing_chat_url() -> None:
    ctx = _Ctx()
    ctx.options = {"thread": "10223", "surface": "claude_ai", "from_agent": "cursor"}
    handler = ContinuityCheckpointResolveHandler()
    out = await handler.execute(_Step(), ctx)
    assert out.json["refused"]["code"] == "checkpoint.chat_url_required"


@pytest.mark.asyncio
async def test_claude_ai_resolve_unclassified_url() -> None:
    ctx = _Ctx()
    ctx.options = {
        "thread": "10223",
        "surface": "claude_ai",
        "from_agent": "cursor",
        "chat_url": "https://example.com/",
    }
    handler = ContinuityCheckpointResolveHandler()
    out = await handler.execute(_Step(), ctx)
    assert out.json["refused"]["code"] == "checkpoint.chat_url_unclassified"


@pytest.mark.asyncio
async def test_omit_transcript_id_refuses() -> None:
    ctx = _Ctx()
    ctx.options = {"thread": "10223", "surface": "cursor", "from_agent": "cursor"}
    handler = ContinuityCheckpointResolveHandler()
    out = await handler.execute(_Step(), ctx)
    assert out.json["refused"]["code"] == "checkpoint.transcript_id_required"


@pytest.mark.asyncio
async def test_transcript_id_binds_jsonl_without_discover(
    tmp_path, monkeypatch
) -> None:
    tid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    jsonl = tmp_path / tid / f"{tid}.jsonl"
    jsonl.parent.mkdir()
    jsonl.write_text("{}\n")
    monkeypatch.setenv("CURSOR_AGENT_TRANSCRIPTS_ROOT", str(tmp_path))
    ctx = _Ctx()
    ctx.options = {
        "thread": "10534",
        "surface": "cursor",
        "from_agent": "cursor",
        "transcript_id": tid,
    }
    handler = ContinuityCheckpointResolveHandler()
    out = await handler.execute(_Step(), ctx)
    assert out.json.get("refused") is None
    assert out.json["jsonl_path"] == f"{tid}/{tid}.jsonl"
    assert out.json["transcript_id"] == tid


@pytest.mark.asyncio
async def test_transcript_id_found_in_satellite_project(tmp_path, monkeypatch) -> None:
    """a:36491 — explicit id under a non-gateway project root still binds."""
    tid = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
    configured = tmp_path / "configured"
    configured.mkdir()
    jsonl = (
        tmp_path
        / "projects"
        / "mnt-torus-projects-claudeburst"
        / "agent-transcripts"
        / tid
        / f"{tid}.jsonl"
    )
    jsonl.parent.mkdir(parents=True)
    jsonl.write_text("{}\n")
    monkeypatch.setenv("CURSOR_AGENT_TRANSCRIPTS_ROOT", str(configured))
    monkeypatch.setattr(
        "cortex_store.transcript_assembly._cursor_projects_root",
        lambda projects_root=None: (tmp_path / "projects").resolve(),
    )
    ctx = _Ctx()
    ctx.options = {
        "thread": "12716",
        "surface": "cursor",
        "from_agent": "cursor",
        "transcript_id": tid,
    }
    handler = ContinuityCheckpointResolveHandler()
    out = await handler.execute(_Step(), ctx)
    assert out.json.get("refused") is None
    assert out.json["jsonl_path"] == str(jsonl.resolve())
    assert out.json["transcript_id"] == tid


def test_duplicate_transcript_id_across_projects_is_ambiguous(tmp_path) -> None:
    from cortex_store.transcript_assembly import locate_cursor_transcript_jsonl

    tid = "cccccccc-dddd-eeee-ffff-000000000001"
    for project in ("alpha", "beta"):
        jsonl = tmp_path / project / "agent-transcripts" / tid / f"{tid}.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("{}\n")
    path, code = locate_cursor_transcript_jsonl(tid, projects_root=tmp_path)
    assert path is None
    assert code == "checkpoint.window_ambiguous"
