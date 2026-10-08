"""Seal-mode graph with fakes: raw, transcribed, rerun, stale card, concurrency."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from continuity_tape.extract_jsonl import extract_turns_from_jsonl_bytes
from continuity_tape.messages import envelope_wire_dict

from ._clients import file_sha256
from .admit import house_lock, run_admit
from .await_verify import run_await_verify
from .card import run_card
from .envelope import run_envelope
from .fact import run_fact
from .post import run_post
from .seal import run_seal
from .source import run_source

pytestmark = pytest.mark.offline

_FIXTURES = Path(__file__).parent / "fixtures"
_YAML = Path(__file__).resolve().parents[1] / "grokbot-checkpoint-v1.yaml"
_CARD = """# house

## Checkpoint log

| n | when | trigger | expected | envelope | pipeline |
|---|---|---|---|---|---|
"""


def _stamp_raw() -> bytes:
    env = extract_turns_from_jsonl_bytes(
        (_FIXTURES / "cursor_window.jsonl").read_bytes(),
        transcript_id="cursor-window",
    )
    env.meta.provenance = "raw"
    env.meta.coverage = "full"
    env.meta.sources = [
        {
            "kind": "jsonl",
            "producer": {
                "id": "cursor-jsonl-extractor",
                "kind": "extractor",
                "version": None,
            },
        }
    ]
    return json.dumps(envelope_wire_dict(env)).encode()


class Bus:
    def __init__(self) -> None:
        self.turns: list[dict] = []
        self.posts = 0

    async def get(self, path: str, params=None):
        return {"turns": list(self.turns)}, 200

    async def send(self, **kwargs):
        self.posts += 1
        turn = {"turn_number": len(self.turns) + 1, "superseded": False, **kwargs}
        turn["from"] = kwargs.get("from_agent")
        self.turns.append(turn)
        return {"turn_number": turn["turn_number"]}, 200


def _options(fs, pour: bytes, *, house: str = "house") -> dict:
    card = _CARD.encode()
    card_uri = f"notes/{house}-card.md"
    pour_uri = f"notes/{house}-pour.json"
    snap_uri = f"notes/{house}-snap.json"
    fs.files[card_uri] = card
    fs.files[pour_uri] = pour
    return {
        "mode": "seal",
        "house": house,
        "n": 1,
        "agent_id": "agent-12345678",
        "bus_identity": "grok-bot",
        "root_thread": "10",
        "verify_lane": "11",
        "verifier": "verifier",
        "trigger": "hop",
        "card_uri": card_uri,
        "expected_card_sha256": file_sha256(card),
        "envelope_ref": pour_uri,
        "envelope_sha256": file_sha256(pour),
        "snapshot_uri": snap_uri,
        "channel": "cp",
        "residue": "r",
    }


async def _seal_graph(fs, options, bus, *, stop_after: str | None = None, dispatch=None):
    admitted = await run_admit(options, {})
    assert admitted["refused"] is None
    source = await run_source(options, fs)
    if source.get("refused") or stop_after == "source":
        return source
    env = await run_envelope(options, source, fs)
    if env.get("refused") or stop_after == "envelope":
        return env
    sealed = await run_seal(
        options,
        env,
        execution_id="exec",
        dispatch=dispatch,
    )
    if stop_after == "seal":
        return {"source": source, "envelope": env, "seal": sealed}
    card = await run_card(
        options, source, env, fs, execution_id="exec", seal=sealed
    )
    post = await run_post(options, sealed, env, get=bus.get, send=bus.send)
    fact = await run_fact(options, card, env, get=bus.get, send=bus.send)
    return {
        "source": source,
        "envelope": env,
        "seal": sealed,
        "card": card,
        "post": post,
        "fact": fact,
    }


@pytest.fixture
def patch_seal(monkeypatch: pytest.MonkeyPatch):
    from . import seal as seal_mod

    monkeypatch.setattr(seal_mod, "stargate_continuity_checkpoint_sealed", lambda **k: None)
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_transcript_seal._stamp_succession_fields",
        lambda **k: None,
    )


@pytest.mark.asyncio
async def test_raw_and_transcribed(fs, patch_seal) -> None:
    raw = _stamp_raw()
    transcribed = (_FIXTURES / "grok_transcribed_pour.json").read_bytes()
    for blob, prov in ((raw, "raw"), (transcribed, "transcribed")):
        bus = Bus()
        options = _options(fs, blob, house=prov)
        out = await _seal_graph(
            fs,
            options,
            bus,
            dispatch=lambda tool, args: _ok_close(),
        )
        assert out["source"]["provenance"] == prov
        assert out["card"]["refused"] is None
        assert prov in out["post"]["body"]
        assert f"provenance {prov}" in out["fact"]["body"]


async def _ok_close():
    return {"session_id": "sess", "already_closed": False, "meta": {"provenance": "raw"}}


@pytest.mark.asyncio
async def test_rerun_after_seal(fs, patch_seal) -> None:
    bus = Bus()
    options = _options(fs, _stamp_raw())
    first = await _seal_graph(
        fs, options, bus, stop_after="seal", dispatch=lambda t, a: _ok_close()
    )
    assert first["seal"]["session_id"] == "sess"

    async def already(tool, args):
        return {"session_id": "sess", "already_closed": True, "meta": {"provenance": "raw"}}

    second = await _seal_graph(fs, options, bus, dispatch=already)
    assert second["envelope"]["action"] == "skip"
    assert second["seal"]["already_closed"] is True
    assert second["card"]["refused"] is None
    assert bus.posts == 2  # CHECKPOINT + FACT
    third = await _seal_graph(fs, options, bus, dispatch=already)
    assert third["post"]["duplicate"] is True
    assert third["fact"]["skipped"] is True
    assert bus.posts == 2


@pytest.mark.asyncio
async def test_stale_card_then_fresh(fs, patch_seal) -> None:
    bus = Bus()
    options = _options(fs, _stamp_raw())
    options["expected_card_sha256"] = "ab" * 32
    out = await _seal_graph(
        fs, options, bus, dispatch=lambda t, a: _ok_close()
    )
    assert out["card"]["refused"]["code"] == "card.sha_mismatch"
    assert options["card_uri"] not in fs.writes
    live = file_sha256(fs.read(options["card_uri"]))
    options["expected_card_sha256"] = live
    again = await _seal_graph(
        fs,
        options,
        bus,
        dispatch=lambda t, a: _async_already(),
    )
    assert again["envelope"]["action"] == "skip"
    assert again["seal"]["already_closed"] is True


async def _async_already():
    return {"session_id": "sess", "already_closed": True, "meta": {"provenance": "raw"}}


@pytest.mark.asyncio
async def test_light_mode_no_pour(fs) -> None:
    card = _CARD.encode()
    fs.files["notes/card.md"] = card
    options = {
        "mode": "light",
        "house": "h",
        "n": 1,
        "card_uri": "notes/card.md",
        "expected_card_sha256": file_sha256(card),
        "trigger": "light",
        "verify_lane": "11",
        "bus_identity": "bot",
        "verifier": "verifier",
    }
    admitted = await run_admit(options, {})
    assert admitted["refused"] is None
    card_out = await run_card(options, {}, {}, fs, execution_id="e")
    assert "none" in card_out["row"]
    assert "notes/pour.json" not in fs.files


@pytest.mark.asyncio
async def test_concurrency_key_serializes() -> None:
    spec = yaml.safe_load(_YAML.read_text())
    assert spec["id"] == "grokbot-checkpoint-v1"
    assert spec["type"] == "grokbot_checkpoint"
    assert spec["concurrency"]["key"] == "grokbot-cp:{options.house}"
    order: list[str] = []

    async def hold(name: str) -> None:
        async with house_lock("same"):
            order.append(f"in-{name}")
            await asyncio.sleep(0.01)
            order.append(f"out-{name}")

    await asyncio.gather(hold("a"), hold("b"))
    assert order[0].startswith("in-")
    assert order[1].startswith("out-")


@pytest.mark.asyncio
async def test_pending_is_graph_tail(fs) -> None:
    now_box = {"t": 0.0}

    def now() -> float:
        return now_box["t"]

    async def sleep(seconds: float) -> None:
        now_box["t"] += seconds

    async def silent(path, params=None):
        return {"turns": []}, 200

    out = await run_await_verify(
        {"n": 1, "verify_lane": "v", "verifier": "verifier", "wait_verify_seconds": 2},
        {"turn": 4},
        get=silent,
        sleep=sleep,
        now=now,
    )
    assert out["state"] == "pending"
