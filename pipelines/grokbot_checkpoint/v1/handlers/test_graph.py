"""Seal-mode graph with fakes: raw, transcribed, rerun, stale card, concurrency."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from continuity_tape.extract_jsonl import extract_turns_from_jsonl_bytes
from continuity_tape.messages import CaptureRef, envelope_wire_dict
from systems.pipeline.core.conditions import evaluate_condition

from ._clients import file_sha256
from .admit import run_admit
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


def _jsonl() -> bytes:
    return (_FIXTURES / "cursor_window.jsonl").read_bytes()


def _stamp_raw() -> bytes:
    raw = _jsonl()
    env = extract_turns_from_jsonl_bytes(raw, transcript_id="cursor-window")
    env.meta.provenance = "raw"
    env.meta.coverage = "full"
    env.meta.capture = CaptureRef(
        kind="jsonl",
        ref="cortex://fixtures/cursor_window.jsonl",
        sha256=file_sha256(raw),
        pseudo_byte_exact=False,
    )
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


def _options(fs, pour: bytes, *, house: str = "house", slot: str = "raw") -> dict:
    card = _CARD.encode()
    card_uri = f"notes/{house}-card.md"
    pour_uri = f"notes/{house}-pour.json"
    snap_uri = f"notes/{house}-snap.json"
    fs.files[card_uri] = card
    fs.files[pour_uri] = pour
    fs.files["fixtures/cursor_window.jsonl"] = _jsonl()
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
        f"{slot}_ref": pour_uri,
        f"{slot}_sha256": file_sha256(pour),
        "snapshot_uri": snap_uri,
        "channel": "cp",
        "residue": "r",
    }


def _out(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(json=payload, raw="", text="", model_id=None, error=None)


async def _seal_graph(fs, options, bus, *, stop_after: str | None = None, dispatch=None):
    """Run steps in YAML order; skip when the YAML condition is false."""
    spec = yaml.safe_load(_YAML.read_text())
    outputs: dict = {}
    calls = {"seal": 0}

    async def step_payload(name: str) -> dict:
        if name == "admit":
            return await run_admit(options, {}, fs=fs, get=bus.get)
        if name == "source":
            return await run_source(options, fs)
        if name == "envelope":
            return await run_envelope(options, outputs["source"].json, fs)
        if name == "seal":
            calls["seal"] += 1
            return await run_seal(
                options,
                outputs["envelope"].json,
                execution_id="exec",
                dispatch=dispatch,
            )
        if name == "card":
            return await run_card(
                options,
                outputs.get("source", _out({})).json,
                outputs.get("envelope", _out({})).json,
                fs,
                execution_id="exec",
                seal=outputs.get("seal", _out({})).json,
            )
        if name == "post":
            return await run_post(
                options,
                outputs["seal"].json,
                outputs["envelope"].json,
                get=bus.get,
                send=bus.send,
            )
        if name == "fact":
            return await run_fact(
                options,
                outputs["card"].json,
                outputs.get("envelope", _out({})).json,
                get=bus.get,
                send=bus.send,
            )
        if name == "await_verify":
            return {"refused": None, "state": "pending", "turn": None}
        if name == "result":
            return {"refused": None}
        return {"refused": None}

    for step in spec["steps"]:
        name = step["name"]
        cond = step.get("condition") or ""
        if cond and not evaluate_condition(cond, outputs, options):
            outputs[name] = _out({"_skipped": True})
            continue
        payload = await step_payload(name)
        outputs[name] = _out(payload)
        if stop_after == name:
            break
    flat = {name: item.json for name, item in outputs.items()}
    flat["seal_calls"] = calls["seal"]
    if flat.get("source", {}).get("refused"):
        return flat["source"] | {"seal_calls": calls["seal"], "posts": bus.posts}
    return flat


@pytest.fixture
def patch_seal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import cortex_store.db as cortex_db
    from cortex_store._test_db_bootstrap import materialize_head_schema_template

    from . import seal as seal_mod

    db_path = tmp_path / "cortex.db"
    materialize_head_schema_template(db_path)
    monkeypatch.setattr(cortex_db, "_CORTEX_DB", db_path)
    cortex_db.run_migrations(cortex_db.cortex_conn())
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
        options = _options(fs, blob, house=prov, slot=prov)
        out = await _seal_graph(
            fs,
            options,
            bus,
            dispatch=lambda tool, args: _ok_close(),
        )
        assert out["source"]["provenance"] == prov
        assert out["source"]["fallback"] is None
        assert out["source"]["alternate"] is None
        assert out["card"]["refused"] is None
        assert prov in out["post"]["body"]
        assert f"provenance {prov}" in out["fact"]["body"]


@pytest.mark.asyncio
async def test_both_slots_and_capture_mismatch(fs, patch_seal) -> None:
    raw = _stamp_raw()
    transcribed = (_FIXTURES / "grok_transcribed_pour.json").read_bytes()
    bus = Bus()
    options = _options(fs, raw, house="both", slot="raw")
    t_uri = "notes/both-transcribed.json"
    fs.files[t_uri] = transcribed
    options["transcribed_ref"] = t_uri
    options["transcribed_sha256"] = file_sha256(transcribed)
    out = await _seal_graph(fs, options, bus, dispatch=lambda tool, args: _ok_close())
    assert out["source"]["provenance"] == "transcribed"
    assert out["source"]["alternate"]["ref"] == options["raw_ref"]
    writes = list(fs.writes)
    bad = _stamp_raw()
    payload = json.loads(bad)
    payload["meta"]["capture"]["sha256"] = "ab" * 32
    payload["meta"]["source_sha256"] = "ab" * 32
    tampered = json.dumps(payload).encode()
    options2 = _options(fs, tampered, house="badcap", slot="raw")
    refused_bus = Bus()
    refused = await _seal_graph(
        fs, options2, refused_bus, dispatch=lambda tool, args: _ok_close()
    )
    assert refused["refused"]["code"] == "capture.sha_mismatch"
    assert refused["seal_calls"] == 0
    assert refused_bus.posts == 0
    assert fs.writes == writes


@pytest.mark.asyncio
async def test_dom_harvest_graph(fs, patch_seal) -> None:
    raw = _jsonl()
    env = extract_turns_from_jsonl_bytes(raw, transcript_id="cursor-window")
    env.meta.provenance = "raw"
    env.meta.coverage = "full"
    env.meta.capture = CaptureRef(
        kind="dom_harvest",
        ref="workspaces://captures/dom.bin",
        sha256=file_sha256(b"dom-bytes"),
        pseudo_byte_exact=True,
    )
    env.meta.sources = [
        {
            "kind": "dom_harvest",
            "producer": {"id": "dom-harvester", "kind": "harvester", "version": None},
        }
    ]
    pour = json.dumps(envelope_wire_dict(env)).encode()
    fs.files["captures/dom.bin"] = b"dom-bytes"
    bus = Bus()
    out = await _seal_graph(
        fs,
        _options(fs, pour, house="dom"),
        bus,
        dispatch=lambda tool, args: _ok_close(),
    )
    assert out["source"]["capture"]["kind"] == "dom_harvest"
    assert "producer dom-harvester" in out["fact"]["body"]
    assert bus.posts == 2


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
    assert third["admit"]["replayed"] is True
    assert third["post"].get("_skipped") is True
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
    live = file_sha256(await fs.read(options["card_uri"]))
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
    async def get(path, params=None):
        return {"turns": []}, 200

    admitted = await run_admit(options, {}, fs=fs, get=get)
    assert admitted["refused"] is None
    card_out = await run_card(options, {}, {}, fs, execution_id="e")
    assert "none" in card_out["row"]
    assert "notes/pour.json" not in fs.files


def test_yaml_names_framework_concurrency_key() -> None:
    spec = yaml.safe_load(_YAML.read_text())
    assert spec["concurrency"]["key"] == "grokbot-cp:{context.dispatch_thread_id}"


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
