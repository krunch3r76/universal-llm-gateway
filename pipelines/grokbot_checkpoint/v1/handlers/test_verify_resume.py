"""verify_resume is read-only and reports per-check pass/fail."""

from __future__ import annotations

import json

import pytest

from ._clients import file_sha256
from .verify_resume import run_verify_resume

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_read_only_checks(fs) -> None:
    card = b"card-bytes"
    snap = json.dumps(
        {"meta": {"messages_sha256": "abc", "provenance": "raw"}}
    ).encode()
    fs.files["notes/card.md"] = card
    fs.files["notes/system/transcripts/grok/h-abcdefghi.raw.messages.json"] = snap
    body = (
        "Window: transcript_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890 · "
        "turns@cp=1 · coverage=full\n"
        "Harvest: transcript:s · messages_sha256:abc · codec:messages-v1 · "
        "surface:cursor · provenance:raw · producer:cursor-jsonl-extractor\n"
    )

    async def get(path, params=None):
        if path.startswith("/threads/") and path.endswith("/turns"):
            return {"detail": "Not Found"}, 404
        if path == "/turns" and params and params.get("thread") == "v":
            return {
                "turns": [
                    {
                        "subject": "CHECKPOINT",
                        "body": body,
                        "turn_number": 2,
                        "from": "bot",
                    },
                    {
                        "subject": "RECEIPT",
                        "body": "ok",
                        "turn_number": 5,
                        "from": "successor",
                    },
                ]
            }, 200
        return {"turns": []}, 200

    writes_before = list(fs.writes)
    out = await run_verify_resume(
        {
            "card_uri": "notes/card.md",
            "expected_card_sha256": file_sha256(card),
            "snapshot_uri": "notes/system/transcripts/grok/h-abcdefghi.raw.messages.json",
            "snapshot_sha256": file_sha256(snap),
            "verify_lane": "v",
            "successor": "successor",
            "fact_turn": 3,
        },
        fs,
        get=get,
    )
    assert fs.writes == writes_before
    assert out["posts"] == 0
    assert all(out["checks"].values())


@pytest.mark.asyncio
async def test_picks_newest_checkpoint_regardless_of_order(fs) -> None:
    card = b"card-bytes"
    snap = json.dumps(
        {"meta": {"messages_sha256": "abc", "provenance": "raw"}}
    ).encode()
    fs.files["notes/card.md"] = card
    fs.files["notes/system/transcripts/grok/h-abcdefghi.raw.messages.json"] = snap
    newer_body = (
        "Window: transcript_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890 · "
        "turns@cp=1 · coverage=full\n"
        "Harvest: transcript:s · messages_sha256:abc · codec:messages-v1 · "
        "surface:cursor · provenance:raw · producer:cursor-jsonl-extractor\n"
    )
    older_body = (
        "Window: transcript_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890 · "
        "turns@cp=1 · coverage=full\n"
        "Harvest: transcript:s · messages_sha256:deadbeef · codec:messages-v1 · "
        "surface:cursor · provenance:raw · producer:cursor-jsonl-extractor\n"
    )

    async def get(path, params=None):
        if path.startswith("/threads/") and path.endswith("/turns"):
            return {"detail": "Not Found"}, 404
        if path == "/turns" and params and params.get("thread") == "v":
            return {
                "turns": [
                    {
                        "subject": "CHECKPOINT",
                        "body": newer_body,
                        "turn_number": 10,
                        "from": "bot",
                    },
                    {
                        "subject": "CHECKPOINT",
                        "body": older_body,
                        "turn_number": 5,
                        "from": "bot",
                    },
                ]
            }, 200
        return {"turns": []}, 200

    out = await run_verify_resume(
        {
            "card_uri": "notes/card.md",
            "expected_card_sha256": file_sha256(card),
            "snapshot_uri": "notes/system/transcripts/grok/h-abcdefghi.raw.messages.json",
            "snapshot_sha256": file_sha256(snap),
            "verify_lane": "v",
            "successor": "successor",
            "fact_turn": 3,
        },
        fs,
        get=get,
    )
    assert out["checks"]["harvest_sha"] is True


def _seal_doc(*, transcript_id: str, provenance: str, messages: list) -> bytes:
    return json.dumps(
        {
            "conversation_uuid": transcript_id,
            "messages": messages,
            "meta": {
                "transcript_id": transcript_id,
                "provenance": provenance,
                "message_count": len(messages),
            },
        }
    ).encode()


def _cp_body(
    *,
    sha: str,
    provenance: str | None,
    transcript_id: str,
    turns: int,
    marker: str,
) -> str:
    prov = f" · provenance:{provenance}" if provenance else ""
    return (
        f"house-cp marker {marker}\n"
        f"Window: transcript_id={transcript_id} · turns@cp={turns} · coverage=full\n"
        f"Harvest: transcript:s · messages_sha256:{sha} · codec:messages-v1 · "
        f"surface:grok{prov} · producer:grok-dom-harvest\n"
    )


@pytest.mark.asyncio
async def test_authority_seal_sha_and_staging_fallback(fs) -> None:
    """V1: re-hash the seal file; staging is a labeled fallback."""
    from ._clients import file_sha256 as sha

    sid = "sess-ck3"
    good = _seal_doc(
        transcript_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        provenance="raw",
        messages=[{"role": "user", "content": "a"}],
    )
    rel = f"notes/system/seals/{sid}.messages.json"
    fs.files[rel] = good
    digest = sha(good)
    card = b"card-bytes"
    fs.files["notes/card.md"] = card

    async def get(path, params=None):
        body = _cp_body(
            sha=digest,
            provenance="raw",
            transcript_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            turns=1,
            marker="house-cp:hm1:1",
        )
        return {
            "turns": [
                {"subject": "CHECKPOINT", "body": body, "turn_number": 2, "from": "bot"}
            ]
        }, 200

    out = await run_verify_resume(
        {
            "card_uri": "notes/card.md",
            "expected_card_sha256": sha(card),
            "session_id": sid,
            "house": "hm1",
            "n": 1,
            "verify_lane": "v",
            "root_thread": "v",
            "successor": "successor",
            "fact_turn": 0,
        },
        fs,
        get=get,
    )
    assert out["checks"]["authority"] == "seal"
    assert out["checks"]["authority_sha"] is True

    fs.files[rel] = good + b" "
    bad = await run_verify_resume(
        {
            "card_uri": "notes/card.md",
            "expected_card_sha256": sha(card),
            "session_id": sid,
            "house": "hm1",
            "n": 1,
            "verify_lane": "v",
            "root_thread": "v",
            "successor": "successor",
            "fact_turn": 0,
        },
        fs,
        get=get,
    )
    assert bad["checks"]["authority_sha"] is False

    del fs.files[rel]
    snap = good
    fs.files["notes/snap.json"] = snap
    fallback = await run_verify_resume(
        {
            "card_uri": "notes/card.md",
            "expected_card_sha256": sha(card),
            "session_id": sid,
            "snapshot_uri": "notes/snap.json",
            "snapshot_sha256": sha(snap),
            "house": "hm1",
            "n": 1,
            "verify_lane": "v",
            "root_thread": "v",
            "successor": "successor",
            "fact_turn": 0,
        },
        fs,
        get=get,
    )
    assert fallback["checks"]["authority"] == "staging_fallback"


@pytest.mark.asyncio
async def test_missing_provenance_is_unknown_warning(fs) -> None:
    """V2: absent provenance token warns; a present mismatch fails."""
    from ._clients import file_sha256 as sha

    sid = "sess-v2"
    doc = _seal_doc(
        transcript_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        provenance="raw",
        messages=[{"role": "user", "content": "a"}],
    )
    rel = f"notes/system/seals/{sid}.messages.json"
    fs.files[rel] = doc
    card = b"card"
    fs.files["notes/card.md"] = card
    digest = sha(doc)

    async def get_for(body: str):
        async def get(path, params=None):
            return {
                "turns": [
                    {
                        "subject": "CHECKPOINT",
                        "body": body,
                        "turn_number": 2,
                        "from": "bot",
                    }
                ]
            }, 200

        return get

    bare = _cp_body(
        sha=digest,
        provenance=None,
        transcript_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        turns=1,
        marker="house-cp:hm1:1",
    )
    out = await run_verify_resume(
        {
            "card_uri": "notes/card.md",
            "expected_card_sha256": sha(card),
            "session_id": sid,
            "house": "hm1",
            "n": 1,
            "verify_lane": "v",
            "root_thread": "v",
            "successor": "x",
            "fact_turn": 9,
        },
        fs,
        get=await get_for(bare),
    )
    assert out["checks"]["provenance"] is True
    assert any("unknown" in w for w in out.get("warnings") or [])

    mismatch = _cp_body(
        sha=digest,
        provenance="transcribed",
        transcript_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        turns=1,
        marker="house-cp:hm1:1",
    )
    bad = await run_verify_resume(
        {
            "card_uri": "notes/card.md",
            "expected_card_sha256": sha(card),
            "session_id": sid,
            "house": "hm1",
            "n": 1,
            "verify_lane": "v",
            "root_thread": "v",
            "successor": "x",
            "fact_turn": 9,
        },
        fs,
        get=await get_for(mismatch),
    )
    assert bad["checks"]["provenance"] is False


@pytest.mark.asyncio
async def test_selects_house_cp_marker_not_newest(fs) -> None:
    """V3: a newer CHECKPOINT with a different house-cp marker is not the tip."""
    from ._clients import file_sha256 as sha

    sid = "sess-v3"
    doc = _seal_doc(
        transcript_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        provenance="raw",
        messages=[{"role": "user", "content": "a"}],
    )
    fs.files[f"notes/system/seals/{sid}.messages.json"] = doc
    card = b"card"
    fs.files["notes/card.md"] = card
    digest = sha(doc)
    ours = _cp_body(
        sha=digest,
        provenance="raw",
        transcript_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        turns=1,
        marker="house-cp:hm1:2",
    )
    newer = _cp_body(
        sha="deadbeef",
        provenance="raw",
        transcript_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        turns=1,
        marker="house-cp:other:9",
    )

    async def get(path, params=None):
        return {
            "turns": [
                {"subject": "CHECKPOINT", "body": ours, "turn_number": 4, "from": "bot"},
                {
                    "subject": "CHECKPOINT",
                    "body": newer,
                    "turn_number": 8,
                    "from": "bot",
                },
            ]
        }, 200

    out = await run_verify_resume(
        {
            "card_uri": "notes/card.md",
            "expected_card_sha256": sha(card),
            "session_id": sid,
            "house": "hm1",
            "n": 2,
            "verify_lane": "v",
            "root_thread": "v",
            "successor": "x",
            "fact_turn": 9,
        },
        fs,
        get=get,
    )
    assert out["checks"]["authority_sha"] is True
    assert out.get("code") != "verify_resume.house_cp_missing"


@pytest.mark.asyncio
async def test_window_transcript_and_turns_match_seal(fs) -> None:
    """V4: Window transcript_id and turns@cp must match the seal."""
    from ._clients import file_sha256 as sha

    sid = "sess-v4"
    tid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    doc = _seal_doc(
        transcript_id=tid,
        provenance="raw",
        messages=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
    )
    fs.files[f"notes/system/seals/{sid}.messages.json"] = doc
    card = b"card"
    fs.files["notes/card.md"] = card
    digest = sha(doc)

    async def run(body: str):
        async def get(path, params=None):
            return {
                "turns": [
                    {
                        "subject": "CHECKPOINT",
                        "body": body,
                        "turn_number": 3,
                        "from": "bot",
                    }
                ]
            }, 200

        return await run_verify_resume(
            {
                "card_uri": "notes/card.md",
                "expected_card_sha256": sha(card),
                "session_id": sid,
                "house": "hm1",
                "n": 1,
                "verify_lane": "v",
                "root_thread": "v",
                "successor": "x",
                "fact_turn": 9,
            },
            fs,
            get=get,
        )

    wrong_id = _cp_body(
        sha=digest,
        provenance="raw",
        transcript_id="bbbbbbbb-e5f6-7890-abcd-ef1234567890",
        turns=2,
        marker="house-cp:hm1:1",
    )
    assert (await run(wrong_id))["checks"]["window_transcript"] is False
    wrong_turns = _cp_body(
        sha=digest, provenance="raw", transcript_id=tid, turns=9, marker="house-cp:hm1:1"
    )
    assert (await run(wrong_turns))["checks"]["window_turns"] is False
    ok = _cp_body(
        sha=digest, provenance="raw", transcript_id=tid, turns=2, marker="house-cp:hm1:1"
    )
    passed = await run(ok)
    assert passed["checks"]["window_transcript"] is True
    assert passed["checks"]["window_turns"] is True
