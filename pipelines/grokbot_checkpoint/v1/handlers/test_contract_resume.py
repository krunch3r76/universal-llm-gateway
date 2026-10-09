"""Contract: grok CHECKPOINT post through real agent-bus store + resume fence."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.checkpoint_projection import (
    CANONICAL_RESUME_FOOTER,
    extract_authored_resume_footer,
)
from agent_bus_store.db import create_thread, create_turn, init_db
from agent_bus_store.resume_fence import arm_resume_fence, assemble_resume_fence
from cortex_store.transcript_cp_anchors import window_anchors_from_text
from fastapi.testclient import TestClient

from .card import run_card
from .post import run_post
from .result import assemble
from .verify_resume import _harvest_provenance, _harvest_sha

pytestmark = pytest.mark.offline

AGENT_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
ROOT = "15902"
SESSION_ID = "grok-bot-hm1-contract-sess"
MSG_SHA = "a" * 64
CAP_SHA = "b" * 64


def _card_body() -> str:
    return (
        "# hm1\n\n## Checkpoint log\n\n"
        "| n | when | trigger | expected | envelope | pipeline |\n"
        "|---|---|---|---|---|---|\n"
    )


def _boot_cortex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cortex_store.db as cortex_db
    from cortex_store._test_db_bootstrap import materialize_head_schema_template

    cortex_path = tmp_path / "cortex.db"
    materialize_head_schema_template(cortex_path)
    monkeypatch.setattr(cortex_db, "_CORTEX_DB", cortex_path)
    cortex_db.run_migrations(cortex_db.cortex_conn())


@pytest.fixture()
def bus_client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _boot_cortex(tmp_path, monkeypatch)
    init_db()
    create_thread(thread_id=ROOT, slug="hm1-root", tags=["role:root"])
    create_turn(
        thread_id=ROOT,
        from_agent="cursor",
        to_agent="cursor",
        subject="open",
        body="seed turn",
        status="open",
    )
    card_dir = tmp_path / "notes/system/threads"
    card_dir.mkdir(parents=True)
    (card_dir / f"{ROOT}-card.md").write_text(_card_body(), encoding="utf-8")
    app = create_app(db_path=str(db_path))
    app.dependency_overrides[require_token] = lambda: None
    with TestClient(app) as client:
        yield client


def _options(**overrides: object) -> dict:
    base = {
        "house": "hm1",
        "n": 1,
        "root_thread": ROOT,
        "agent_id": AGENT_ID,
        "bus_identity": "grok-bot-hm1",
        "verifier": "cursor",
        "residue": "pipeline residue for contract",
        "channel": "cp",
    }
    base.update(overrides)
    return base


def _seal() -> dict:
    return {"session_id": SESSION_ID}


def _envelope(**overrides: object) -> dict:
    base = {
        "snapshot_uri": "notes/system/transcripts/grok/hm1-abcdefghi.raw.messages.json",
        "messages_sha256": MSG_SHA,
        "surface": "grok",
        "provenance": "raw",
        "producer": "grok-dom-harvest",
        "turn_count": 3,
        "coverage": "full",
        "capture": {
            "kind": "dom_harvest",
            "sha256": CAP_SHA,
            "pseudo_byte_exact": True,
        },
    }
    base.update(overrides)
    return base


def _adapters(client: TestClient):
    async def get(path: str, params=None):
        if params is not None:
            resp = client.get(path, params=params)
            return resp.json(), resp.status_code
        if path.startswith("/threads/") and path.endswith("/turns"):
            thread_id = path.split("/")[2]
            resp = client.get("/turns", params={"thread": thread_id})
            if resp.status_code >= 400:
                return resp.json(), resp.status_code
            data = resp.json()
            turns = data.get("turns") if isinstance(data, dict) else []
            return {"turns": turns}, resp.status_code
        resp = client.get(path)
        return resp.json(), resp.status_code

    async def send(**kwargs):
        payload = {
            "thread": kwargs["thread"],
            "from": kwargs["from_agent"],
            "to": kwargs["to_agent"],
            "subject": kwargs["subject"],
            "body": kwargs["body"],
        }
        if kwargs.get("after_turn") is not None:
            payload["after_turn"] = kwargs["after_turn"]
        resp = client.post("/threads/send", json=payload)
        return resp.json(), resp.status_code

    return get, send


def _turn_number(send_payload: dict) -> int:
    if "turn_number" in send_payload:
        return int(send_payload["turn_number"])
    turn = send_payload.get("turn") or {}
    if isinstance(turn, dict):
        return int(turn.get("turn_number") or 0)
    return 0


@pytest.mark.asyncio
async def test_grok_tip_is_resume_readable(bus_client) -> None:
    get, send = _adapters(bus_client)
    posted_calls: list[dict] = []

    def _spy(**kwargs):
        posted_calls.append(kwargs)

    import pipelines.grokbot_checkpoint.v1.handlers.post as post_mod

    with patch.object(
        post_mod,
        "stargate_continuity_checkpoint_posted",
        side_effect=_spy,
    ):
        out = await run_post(
            _options(),
            _seal(),
            _envelope(),
            get=get,
            send=send,
            execution_id="exec-contract-1",
        )
        assert len(posted_calls) == 1
        assert posted_calls[0]["bus_turn"] == out["turn"]
        assert posted_calls[0]["surface"] == "grok"
        assert posted_calls[0]["execution_id"] == "exec-contract-1"
        assert posted_calls[0]["thread"] == ROOT
    assert out.get("refused") is None
    assert out.get("duplicate") is False
    turn = out["turn"]
    assert turn is not None

    tip_resp = bus_client.get(
        "/turns/by-number",
        params={"thread": ROOT, "turn_number": "latest"},
    )
    assert tip_resp.status_code == 200, tip_resp.text
    stored = tip_resp.json()
    stored_body = stored["body"]
    assert stored["subject"].upper().startswith("CHECKPOINT")
    assert stored["turn_number"] == turn

    anchors = window_anchors_from_text(stored_body)
    assert anchors == ((AGENT_ID, 3),)

    mission_lines = [ln for ln in stored_body.splitlines() if ln.startswith("Mission:")]
    assert len(mission_lines) == 1
    assert extract_authored_resume_footer(stored_body) == CANONICAL_RESUME_FOOTER

    assert _harvest_sha(stored_body) == MSG_SHA
    assert _harvest_provenance(stored_body) == "raw"

    armed = arm_resume_fence(ROOT, transcript_id=AGENT_ID, source="hook_prompt")
    bundle = assemble_resume_fence(ROOT, transcript_id=AGENT_ID, source="hook_prompt")
    assert bundle["tip_checkpoint"]["turn_number"] == stored["turn_number"]
    window = bundle["mission"]["window_anchor"]
    assert window is not None
    assert window["transcript_id"] == AGENT_ID
    assert window["turns_at_cp"] == 3
    entities = bundle["read_set"]["readable"]["entities"]
    assert f"transcript:{SESSION_ID}" in entities
    card_uri = f"cortex://notes/system/threads/{ROOT}-card.md"
    assert card_uri in bundle["read_set"]["readable"]["cortex_uris"] or (
        bundle.get("card") or {}
    ).get("uri") == card_uri
    _ = armed  # arm side effect consumed by assemble


@pytest.mark.asyncio
async def test_stale_tip_409_retries_once(bus_client) -> None:
    get, base_send = _adapters(bus_client)
    send_log: list[int | None] = []
    statuses: list[int] = []
    calls = {"n": 0}

    async def send(**kwargs):
        if calls["n"] == 0:
            bus_client.post(
                "/turns",
                json={
                    "thread": ROOT,
                    "from": "cursor",
                    "to": "grok-bot-hm1",
                    "subject": "stale unread",
                    "body": "blocks after_turn",
                },
            )
            calls["n"] += 1
        send_log.append(kwargs.get("after_turn"))
        payload, status = await base_send(**kwargs)
        statuses.append(status)
        return payload, status

    with patch(
        "pipelines.grokbot_checkpoint.v1.handlers.post.stargate_continuity_checkpoint_posted",
    ):
        out = await run_post(
            _options(),
            _seal(),
            _envelope(),
            get=get,
            send=send,
            execution_id="exec-409-once",
        )
    assert statuses[0] == 409
    assert statuses[1] == 201
    assert send_log[1] == 2
    assert out.get("refused") is None
    assert out.get("turn") is not None


@pytest.mark.asyncio
async def test_stale_tip_409_twice_refuses(bus_client) -> None:
    get, base_send = _adapters(bus_client)
    calls = {"n": 0}

    async def send(**kwargs):
        bus_client.post(
            "/turns",
            json={
                "thread": ROOT,
                "from": "cursor",
                "to": "grok-bot-hm1",
                "subject": f"stale-{calls['n']}",
                "body": "block",
            },
        )
        calls["n"] += 1
        return await base_send(**kwargs)

    with patch(
        "pipelines.grokbot_checkpoint.v1.handlers.post.stargate_continuity_checkpoint_posted",
    ):
        out = await run_post(
            _options(),
            _seal(),
            _envelope(),
            get=get,
            send=send,
            execution_id="exec-409-twice",
        )
    assert out["refused"]["code"] == "post.bus"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_user_speech_refused_422(bus_client) -> None:
    get, send = _adapters(bus_client)
    with patch(
        "pipelines.grokbot_checkpoint.v1.handlers.post.stargate_continuity_checkpoint_posted",
    ) as spy:
        out = await run_post(
            _options(residue="line\n### User\nspoken\n"),
            _seal(),
            _envelope(),
            get=get,
            send=send,
            execution_id="exec-422",
        )
    assert out["refused"]["code"] == "post.bus"
    spy.assert_not_called()
    tip_resp = bus_client.get(
        "/turns/by-number",
        params={"thread": ROOT, "turn_number": "latest"},
    )
    assert tip_resp.json()["turn_number"] == 1


@pytest.mark.asyncio
async def test_card_binding_warnings() -> None:
    from .conftest import MemFs
    from ._clients import file_sha256

    env = _envelope()
    seal = _seal()

    fs_ok = MemFs()
    card = _card_body()
    fs_ok.files[f"notes/system/threads/{ROOT}-card.md"] = card.encode()
    expected = file_sha256(card.encode())
    ok = await run_card(
        {
            "card_uri": f"notes/system/threads/{ROOT}-card.md",
            "expected_card_sha256": expected,
            "n": 1,
            "mode": "seal",
            "trigger": "hop",
            "root_thread": ROOT,
            "house": "hm1",
        },
        {},
        env,
        fs_ok,
        execution_id="e-card",
        seal=seal,
    )
    assert ok.get("warnings") == []

    fs_nc = MemFs()
    fs_nc.files["notes/system/threads/15902-continuity.md"] = card.encode()
    nonconf = await run_card(
        {
            "card_uri": "notes/system/threads/15902-continuity.md",
            "expected_card_sha256": expected,
            "n": 1,
            "mode": "seal",
            "trigger": "hop",
            "root_thread": ROOT,
            "house": "hm1",
            "card_body": card,
        },
        {},
        env,
        fs_nc,
        execution_id="e-nonconf",
        seal=seal,
    )
    assert nonconf["warnings"][0]["code"] == "card.nonconforming"

    fs_ub = MemFs()
    fs_ub.files["notes/houses/orion/card.md"] = card.encode()
    unbound = await run_card(
        {
            "card_uri": "notes/houses/orion/card.md",
            "expected_card_sha256": expected,
            "n": 1,
            "mode": "seal",
            "trigger": "hop",
            "root_thread": ROOT,
            "house": "hm1",
        },
        {},
        env,
        fs_ub,
        execution_id="e-unbound",
        seal=seal,
    )
    assert unbound["warnings"][0]["code"] == "card.resume_unbound"

    top = assemble(
        _options(card_uri="notes/system/threads/15902-continuity.md"),
        {
            "card": {"json": nonconf},
            "admit": {"json": {"replayed": False}},
        },
    )
    assert top["warnings"] == nonconf["warnings"]
