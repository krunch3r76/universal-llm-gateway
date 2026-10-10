"""Card row append, sha mismatch, and cp-n replay."""

from __future__ import annotations

import pytest

from ._clients import file_sha256
from .card import run_card

pytestmark = pytest.mark.offline

_CARD = """# house

## Checkpoint log

| n | when | trigger | expected | envelope | pipeline |
|---|---|---|---|---|---|
"""


@pytest.mark.asyncio
async def test_append_mismatch_and_replay(fs) -> None:
    fs.files["notes/card.md"] = _CARD.encode()
    expected = file_sha256(_CARD.encode())
    options = {
        "card_uri": "notes/card.md",
        "expected_card_sha256": expected,
        "n": 1,
        "mode": "seal",
        "trigger": "hop",
        "bus_identity": "grok-bot-hm1",
    }
    env = {
        "snapshot_uri": "notes/system/transcripts/grok/h-abcdefghi.raw.messages.json",
        "sha256": "snapsha",
        "messages_sha256": "msgsha",
        "provenance": "raw",
        "producer": "cursor-jsonl-extractor",
    }
    stale = await run_card(
        {**options, "expected_card_sha256": "f" * 64},
        {},
        env,
        fs,
        execution_id="e0",
        seal={"session_id": "s", "refused": None},
    )
    assert stale["refused"]["code"] == "card.sha_mismatch"
    assert fs.writes == []
    out = await run_card(
        options,
        {},
        env,
        fs,
        execution_id="e1",
        seal={"session_id": "s", "refused": None},
    )
    assert out["refused"] is None
    body = (await fs.read("notes/card.md")).decode()
    assert body.count("| 1 |") == 1
    assert "provenance:raw" in body
    assert "attribution:grok-bot-hm1" in body
    untouched = await fs.read("notes/card.md")
    replay = await run_card(
        {**options, "expected_card_sha256": "f" * 64, "n": 1},
        {},
        env,
        fs,
        execution_id="e2",
        seal={"session_id": "s", "refused": None},
    )
    assert replay["replayed"] is True
    assert await fs.read("notes/card.md") == untouched
    assert "pipeline e2" not in body


@pytest.mark.asyncio
async def test_card_records_event_suppression(fs) -> None:
    fs.files["notes/card.md"] = _CARD.encode()
    options = {
        "card_uri": "notes/card.md",
        "expected_card_sha256": file_sha256(_CARD.encode()),
        "n": 1,
        "mode": "seal",
        "trigger": "hop",
        "bus_identity": "grok-bot-hm1",
    }
    env = {
        "snapshot_uri": "notes/snap.json",
        "sha256": "snapsha",
        "messages_sha256": "msgsha",
        "provenance": "raw",
        "producer": "cursor-jsonl-extractor",
    }
    out = await run_card(
        options,
        {},
        env,
        fs,
        execution_id="e3",
        seal={
            "session_id": "s",
            "refused": None,
            "event_suppressed_reason": "prior_read_failed",
        },
    )
    assert out["refused"] is None
    assert "event_suppressed:prior_read_failed" in out["row"]
    assert "attribution:grok-bot-hm1" in out["row"]


@pytest.mark.asyncio
async def test_card_row_cites_seal_not_staging(fs) -> None:
    """F3b: durable row cites the seal path; staging: trips the audit token."""
    from cortex_store.dispatch_ops._detectors.provenance_staging import (
        _STAGING_TOKEN_RE,
    )

    fs.files["notes/card.md"] = _CARD.encode()
    options = {
        "card_uri": "notes/card.md",
        "expected_card_sha256": file_sha256(_CARD.encode()),
        "n": 1,
        "mode": "seal",
        "trigger": "hop",
        "bus_identity": "grok-bot-hm1",
    }
    env = {
        "snapshot_uri": "ephemeral/harvests/grok/h-abcdefghi.raw.messages.json",
        "sha256": "snapsha",
        "messages_sha256": "msgsha",
        "provenance": "raw",
        "producer": "grok-dom-harvest",
    }
    out = await run_card(
        options,
        {},
        env,
        fs,
        execution_id="e4",
        seal={"session_id": "sess-f3b", "refused": None},
    )
    row = out["row"]
    assert "staging:" not in row
    assert "notes/system/seals/sess-f3b.messages.json" in row
    assert _STAGING_TOKEN_RE.search(row) is None
    assert out.get("snapshot_uri") == env["snapshot_uri"]
    old = (
        "notes/system/seals/sess-f3b.messages.json · "
        "staging:ephemeral/harvests/grok/h-abcdefghi.raw.messages.json@snapsha"
    )
    assert "staging:" in old
    assert _STAGING_TOKEN_RE.search(row) is None
    assert _STAGING_TOKEN_RE.search("dropbox/harvests/grok/x")


_V2 = "# house v2\n\nstanding rules live here.\n"


@pytest.mark.asyncio
async def test_n1_without_log_appends_header(fs) -> None:
    fs.files["notes/card.md"] = _V2.encode()
    expected = file_sha256(_V2.encode())
    out = await run_card(
        {
            "card_uri": "notes/card.md",
            "expected_card_sha256": expected,
            "n": 1,
            "mode": "light",
            "trigger": "hop",
        },
        {},
        {},
        fs,
        execution_id="e-log",
    )
    assert out["refused"] is None
    body = (await fs.read("notes/card.md")).decode()
    assert "\n\n## Checkpoint log\n\n" in body
    assert "| n | when | trigger | prior card sha | seal | by |" in body
    assert "| 1 |" in body
    stale = await run_card(
        {
            "card_uri": "notes/card.md",
            "expected_card_sha256": expected,
            "n": 1,
            "mode": "light",
            "trigger": "hop",
        },
        {},
        {},
        fs,
        execution_id="e-log-2",
    )
    assert stale.get("replayed") is True
    assert (await fs.read("notes/card.md")).decode().count("| 1 |") == 1
    guarded = await run_card(
        {
            "card_uri": "notes/card.md",
            "expected_card_sha256": "ab" * 32,
            "n": 2,
            "mode": "light",
            "trigger": "hop",
        },
        {},
        {},
        fs,
        execution_id="e-log-3",
    )
    assert guarded["refused"]["code"] == "card.sha_mismatch"


@pytest.mark.asyncio
async def test_n2_without_log_refuses(fs) -> None:
    fs.files["notes/card.md"] = _V2.encode()
    out = await run_card(
        {
            "card_uri": "notes/card.md",
            "expected_card_sha256": file_sha256(_V2.encode()),
            "n": 2,
            "mode": "light",
            "trigger": "hop",
        },
        {},
        {},
        fs,
        execution_id="e-n2",
    )
    assert out["refused"]["code"] == "card.body_invalid"


@pytest.mark.asyncio
async def test_card_body_guard_uses_live_sha(fs) -> None:
    live = _V2
    supplied = "# supplied\n\nother body\n"
    fs.files["notes/card.md"] = live.encode()
    live_sha = file_sha256(live.encode())
    out = await run_card(
        {
            "card_uri": "notes/card.md",
            "expected_card_sha256": live_sha,
            "card_body": supplied,
            "n": 1,
            "mode": "light",
            "trigger": "hop",
        },
        {},
        {},
        fs,
        execution_id="e-body",
    )
    assert out["refused"] is None
    written = (await fs.read("notes/card.md")).decode()
    assert written.startswith("# supplied")
    assert "## Checkpoint log" in written
    fs.files["notes/other.md"] = live.encode()
    wrong = await run_card(
        {
            "card_uri": "notes/other.md",
            "expected_card_sha256": file_sha256(supplied.encode()),
            "card_body": supplied,
            "n": 1,
            "mode": "light",
            "trigger": "hop",
        },
        {},
        {},
        fs,
        execution_id="e-body-mismatch",
    )
    assert wrong["refused"]["code"] == "card.sha_mismatch"
