"""Handoff letter: admit, snapshot overwrite, post refusal, verify report."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from continuity_tape.messages import seal_messages_sha256

from .envelope import run_envelope
from .post import run_post
from .verify_resume import run_verify_resume
from .admit import run_admit

pytestmark = pytest.mark.offline

_URI = "notes/system/transcripts/grok/h-abcdefghi.raw.messages.json"
_MSGS = [{"role": "user", "content": "hello", "turn_index": 1}]


def _letter(messages: list[dict], **overrides: object) -> dict:
    letter = {
        "house_cp": "house-cp:h:1",
        "messages_sha256": seal_messages_sha256(messages),
        "as_of_message_index": len(messages),
        "author": "grok-bot-hm1",
        "mission": "resume the house",
        "state": "open",
        "next_acts": ["read the card"],
        "load_bearing": ["intent is not in the transcript"],
    }
    letter.update(overrides)
    return letter


def _sha12(letter: dict) -> str:
    raw = json.dumps(
        letter, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    return hashlib.sha256(raw).hexdigest()[:12]


def _env(messages: list[dict]) -> dict:
    return {
        "schema": "ulg.continuity.messages/1",
        "messages": messages,
        "meta": {
            "messages_sha256": seal_messages_sha256(messages),
            "provenance": "raw",
        },
    }


def _source(messages: list[dict]) -> dict:
    env = _env(messages)
    return {
        "envelope": env,
        "messages_sha256": env["meta"]["messages_sha256"],
        "provenance": "raw",
        "producer": "cursor-jsonl-extractor",
        "coverage": "full",
        "turn_count": 1,
        "surface": "grok",
    }


def _options(**extra: object) -> dict:
    base = {
        "snapshot_uri": _URI,
        "house": "h",
        "n": 1,
        "agent_id": "abcdefghi",
        "mode": "seal",
    }
    base.update(extra)
    return base


@pytest.mark.asyncio
async def test_bound_letter_is_stored_and_cited(fs) -> None:
    letter = _letter(_MSGS, kept_extra="stay")
    out = await run_envelope(_options(handoff=letter), _source(_MSGS), fs)
    assert out["handoff_binding"] == "bound"
    stored = json.loads((await fs.read(_URI)).decode())
    assert stored["handoff"] == letter

    sent: list[dict] = []

    async def get(path, params=None):
        if path == "/turns/by-number":
            return {"turn_number": 0}, 200
        return {"turns": []}, 200

    async def send(**kwargs):
        sent.append(kwargs)
        return {"turn": {"turn_number": 1}}, 200

    posted = await run_post(
        {
            "house": "h",
            "n": 1,
            "root_thread": "9",
            "agent_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "bus_identity": "grok-bot-hm1",
            "verifier": "verifier",
        },
        {"session_id": "sess"},
        out,
        get=get,
        send=send,
    )
    assert posted["refused"] is None
    assert f"handoff:{_sha12(letter)}" in posted["body"]

    seal_rel = "notes/system/seals/sess.messages.json"
    fs.files[seal_rel] = json.dumps(stored).encode()

    async def bus_get(path, params=None):
        return {"turns": []}, 200

    verified = await run_verify_resume(
        {
            "house": "h",
            "n": 1,
            "session_id": "sess",
            "card_uri": "missing-card",
            "expected_card_sha256": "0",
            "snapshot_uri": _URI,
            "snapshot_sha256": out["sha256"],
        },
        fs,
        get=bus_get,
    )
    assert verified["checks"]["handoff"] == "bound"


@pytest.mark.asyncio
async def test_stale_letter_refuses_post_before_bus(fs) -> None:
    short = _letter(_MSGS, as_of_message_index=len(_MSGS) - 1 or 0)
    short["as_of_message_index"] = 0
    # index 0 is invalid at admit; binding treats a present letter with a short index as stale
    short["as_of_message_index"] = len(_MSGS) - 1 if len(_MSGS) > 1 else 0
    other = _letter(
        [{"role": "user", "content": "other"}],
        as_of_message_index=len(_MSGS),
        house_cp="house-cp:h:1",
    )
    out = await run_envelope(_options(handoff=other), _source(_MSGS), fs)
    assert out["handoff_binding"] == "stale"
    assert "messages_sha256" in out["handoff_reasons"]

    called = {"n": 0}

    async def get(path, params=None):
        called["n"] += 1
        return {"turns": []}, 200

    async def send(**kwargs):
        called["n"] += 1
        return {"turn": {"turn_number": 1}}, 200

    posted = await run_post(
        {
            "house": "h",
            "n": 1,
            "root_thread": "9",
            "bus_identity": "grok-bot-hm1",
            "verifier": "verifier",
        },
        {"session_id": "sess"},
        out,
        get=get,
        send=send,
    )
    assert posted["refused"]["code"] == "post.handoff_stale"
    assert "messages_sha256" in str(posted["refused"])
    assert called["n"] == 0

    short_index = _letter(_MSGS, as_of_message_index=max(len(_MSGS) - 1, 0))
    if len(_MSGS) == 1:
        short_index["as_of_message_index"] = 0
    out_index = await run_envelope(
        _options(handoff=short_index, snapshot_uri=_URI + ".b"),
        _source(_MSGS),
        fs,
    )
    assert out_index["handoff_binding"] == "stale"
    assert "as_of_message_index" in out_index["handoff_reasons"]


@pytest.mark.asyncio
async def test_missing_letter_completes_with_warning(fs) -> None:
    out = await run_envelope(_options(), _source(_MSGS), fs)
    assert out["handoff_binding"] == "missing"
    assert "handoff" not in json.loads((await fs.read(_URI)).decode())

    async def get(path, params=None):
        if path == "/turns/by-number":
            return {"turn_number": 0}, 200
        return {"turns": []}, 200

    async def send(**kwargs):
        return {"turn": {"turn_number": 1}}, 200

    posted = await run_post(
        {
            "house": "h",
            "n": 1,
            "root_thread": "9",
            "bus_identity": "bot",
            "verifier": "verifier",
        },
        {"session_id": "sess"},
        out,
        get=get,
        send=send,
    )
    assert posted["refused"] is None
    assert "handoff:none" in posted["body"]

    seal_rel = "notes/system/seals/sess.messages.json"
    fs.files[seal_rel] = await fs.read(_URI)

    verified = await run_verify_resume(
        {
            "house": "h",
            "n": 1,
            "session_id": "sess",
            "card_uri": "missing",
            "expected_card_sha256": "0",
            "snapshot_uri": _URI,
            "snapshot_sha256": out["sha256"],
        },
        fs,
        get=get,
    )
    assert verified["checks"]["handoff"] == "missing"
    assert any(
        isinstance(item, dict) and item.get("code") == "verify_resume.handoff_missing"
        for item in verified["warnings"]
    )
    assert verified["refused"] is None
    assert "handoff_reasons" not in verified["checks"]


@pytest.mark.asyncio
async def test_same_messages_new_letter_overwrites(fs) -> None:
    first = _letter(_MSGS)
    wrote = await run_envelope(_options(handoff=first), _source(_MSGS), fs)
    assert wrote["action"] == "write"
    same = await run_envelope(_options(handoff=first), _source(_MSGS), fs)
    assert same["action"] == "skip"
    nxt = _letter(_MSGS, mission="a later mission")
    over = await run_envelope(_options(handoff=nxt), _source(_MSGS), fs)
    assert over["action"] == "overwrite"
    stored = json.loads((await fs.read(_URI)).decode())
    assert stored["handoff"] == nxt
    gone = await run_envelope(_options(), _source(_MSGS), fs)
    assert gone["action"] == "overwrite"
    assert "handoff" not in json.loads((await fs.read(_URI)).decode())


def _admit(**extra: object) -> dict:
    base = {
        "mode": "seal",
        "house": "h",
        "n": 1,
        "agent_id": "11111111-1111-4111-8111-111111111111",
    }
    base.update(extra)
    return base


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    [
        "house_cp",
        "messages_sha256",
        "as_of_message_index",
        "author",
        "mission",
        "state",
        "next_acts",
        "load_bearing",
    ],
)
async def test_admit_invalid_field(mutate: str) -> None:
    letter = _letter(_MSGS)
    if mutate == "house_cp":
        letter["house_cp"] = "nope"
    elif mutate == "messages_sha256":
        letter["messages_sha256"] = "zz"
    elif mutate == "as_of_message_index":
        letter["as_of_message_index"] = 0
    elif mutate == "author":
        letter["author"] = ""
    elif mutate == "mission":
        letter["mission"] = "two\nlines"
    elif mutate == "state":
        letter["state"] = 1
    elif mutate == "next_acts":
        letter["next_acts"] = []
    else:
        del letter["load_bearing"]
    out = await run_admit(_admit(handoff=letter), {})
    assert out["refused"]["code"] == "admit.handoff_invalid"
    assert mutate in out["refused"]["message"]


@pytest.mark.asyncio
async def test_admit_empty_load_bearing_needs_reason() -> None:
    letter = _letter(_MSGS, load_bearing=[])
    out = await run_admit(_admit(handoff=letter), {})
    assert out["refused"]["code"] == "admit.handoff_invalid"
    assert "load_bearing" in out["refused"]["message"]
    letter["load_bearing_reason"] = "transcript already has the facts"
    ok = await run_admit(_admit(handoff=letter), {})
    assert ok["refused"] is None


@pytest.mark.asyncio
async def test_admit_too_large_and_light_mode() -> None:
    from . import _handoff

    letter = _letter(_MSGS, mission="m" * (_handoff.HANDOFF_MAX_BYTES + 8))
    out = await run_admit(_admit(handoff=letter), {})
    assert out["refused"]["code"] == "admit.handoff_too_large"
    light = await run_admit(_admit(mode="light", handoff=_letter(_MSGS)), {})
    assert light["refused"]["code"] == "admit.handoff_mode"


@pytest.mark.asyncio
async def test_seal_file_keeps_handoff(monkeypatch, tmp_path: Path, fs) -> None:
    from .test_contract import _boot, _Spy, _patch_event
    from .seal import run_seal

    _boot(monkeypatch, tmp_path)
    spy = _Spy()
    _patch_event(monkeypatch, spy)
    messages = [
        {
            "role": "user",
            "content": "turn one",
            "turn_index": 1,
            "timestamp": "2026-04-05T06:07:08Z",
        }
    ]
    letter = _letter(messages, house_cp="house-cp:hm1:1")
    env = _env(messages)
    env["meta"]["surface"] = "grok"
    env["meta"]["opened_at"] = "2026-04-05T06:07:08Z"
    env["meta"]["turn_count"] = 1
    env["handoff"] = letter
    step = {
        "snapshot_uri": "ephemeral/harvests/grok/hm1.raw.messages.json",
        "envelope": env,
        "messages_sha256": env["meta"]["messages_sha256"],
        "surface": "grok",
        "turn_count": 1,
        "coverage": "full",
        "provenance": "raw",
        "producer": "contract",
        "sha256": "staging",
        "handoff_binding": "bound",
    }
    out = await run_seal(
        {
            "bus_identity": "grok-bot-hm1",
            "agent_id": "agent-handoff-1",
            "house": "hm1",
            "n": 1,
            "root_thread": "15903",
        },
        step,
        execution_id="e",
        dispatch=spy.dispatch,
    )
    assert out["refused"] is None
    seal_path = tmp_path / "notes" / "system" / "seals" / f"{out['session_id']}.messages.json"
    stored = json.loads(seal_path.read_text(encoding="utf-8"))
    assert stored["handoff"] == letter
