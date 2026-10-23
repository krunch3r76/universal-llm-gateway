"""Hop seating writes the lane CSE association; a store miss does not fail the poll."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import init_db
from agent_bus_store.db.cse_associations import HOP_SEATED_BOUND_BY
from agent_bus_store.server import create_app
from claude_bundles.cdp_model_endpoint import CdpGenerateResult
from fastapi.testclient import TestClient

from systems.frontier_consult import cdp_hop_cse_bind
from systems.frontier_consult.cdp_hop_cse_bind import associate_hop_seated_cse

_SUCCESSOR = "https://claude.ai/cowork/cse_01Cir4NQUXr8sPrwmGJhXyak"
_REG = "5381d87f06824ced9e9052a94f9934c2"


def test_non_hop_seating_does_not_associate() -> None:
    with patch("agent_bus_store.db.cse_associations.associate_cse") as associate:
        associate_hop_seated_cse(
            mission_kind="root",
            thread_id="12286",
            parent_thread="12286",
            chat_url=_SUCCESSOR,
            registration_id=_REG,
        )
    associate.assert_not_called()


def test_empty_chat_url_does_not_associate() -> None:
    with patch("agent_bus_store.db.cse_associations.associate_cse") as associate:
        associate_hop_seated_cse(
            mission_kind="hop",
            thread_id="12286",
            parent_thread="12286",
            chat_url="  ",
            registration_id=_REG,
        )
    associate.assert_not_called()


def test_hop_seated_uses_parent_thread_and_mint_bound_by() -> None:
    with patch("agent_bus_store.db.cse_associations.associate_cse") as associate:
        associate_hop_seated_cse(
            mission_kind="hop",
            thread_id="child-dispatch",
            parent_thread="12286",
            chat_url=_SUCCESSOR,
            registration_id=_REG,
        )
    associate.assert_called_once_with(
        thread_id="12286",
        cse_chat_url=_SUCCESSOR,
        cse_registration_id=_REG,
        bound_by=HOP_SEATED_BOUND_BY,
        evidence="cdp.generate.seated",
    )


def test_missing_thread_does_not_raise() -> None:
    with patch(
        "agent_bus_store.db.cse_associations.associate_cse",
        side_effect=LookupError("Thread missing"),
    ):
        associate_hop_seated_cse(
            mission_kind="hop",
            thread_id="12286",
            parent_thread="12286",
            chat_url=_SUCCESSOR,
            registration_id=_REG,
        )


def test_hop_seated_posts_tab_bind_seat_registration_turn(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tab bind posts build_seat_registration_stamp on the hop lane (B1)."""
    from agent_bus_store.db import create_thread, get_turns

    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="hop-seat-stamp")
    assert detail is not None
    thread_id = str(detail["id"])
    birth = "c" * 32
    cdp_hop_cse_bind._STAMP_POSTED.clear()
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        associate_hop_seated_cse(
            mission_kind="hop",
            thread_id="child",
            parent_thread=thread_id,
            chat_url=_SUCCESSOR,
            registration_id=_REG,
            execution_id="exec-hop-stamp",
            successor_birth_id=birth,
        )
    turns = get_turns(thread=thread_id, last=5)
    stamp_turns = [
        t
        for t in turns
        if str(t.get("body") or "").startswith("TYPE: SEAT_REGISTRATION")
    ]
    assert len(stamp_turns) == 1
    body = stamp_turns[0]["body"]
    assert f"registration_id: {_REG}" in body
    assert f"successor_birth_id: {birth}" in body
    assert _SUCCESSOR in body


def test_hop_seated_thread_get_matches_successor(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After hop seating, thread_get names the successor and bound_by is the mint path.

    Breaks when the helper writes thread_id instead of parent_thread, when
    bound_by is the relay agent, or when the row is not the folded current.
    """
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    client = TestClient(app)
    created = client.post("/threads", json={"slug": "hop-seat"})
    assert created.status_code == 201, created.text
    thread_id = created.json()["id"]

    with patch("agent_bus_store.db.cse_associations.emit_cse_bound") as emit:
        associate_hop_seated_cse(
            mission_kind="hop",
            thread_id="other",
            parent_thread=thread_id,
            chat_url=_SUCCESSOR,
            registration_id=_REG,
        )

    detail = client.get(f"/threads/{thread_id}").json()
    assert detail["cse_chat_url"] == _SUCCESSOR
    assert detail["cse_registration_id"] == _REG
    emit.assert_called_once()
    assert emit.call_args.kwargs["bound_by"] == HOP_SEATED_BOUND_BY
    assert emit.call_args.kwargs["bound_by"] != "web-anthropic"
    assert emit.call_args.kwargs["cse_chat_url"] == _SUCCESSOR
    assert emit.call_args.kwargs["cse_registration_id"] == _REG


@pytest.mark.asyncio
async def test_worker_seated_hook_calls_hop_associate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seated callback, not request time, is what writes the hop association.

    Breaks when on_url_bound drops mission_kind or parent_thread, or when a
    non-seated generate path is the only caller.
    """
    from systems.frontier_consult import cdp_generate_worker as worker_mod

    seen: dict[str, Any] = {}

    def _fake_generate(**kwargs: Any) -> CdpGenerateResult:
        kwargs["on_url_bound"](_SUCCESSOR, _REG, 1)
        return CdpGenerateResult(
            ok=True,
            body="ok",
            execution_id=str(kwargs["execution_id"]),
            satellite_execution_id=None,
            prompt_uri=str(kwargs["prompt_uri"]),
            picker_model="opus-5",
        )

    def _record_bind(**kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(worker_mod, "run_cdp_generate", _fake_generate)
    monkeypatch.setattr(worker_mod, "publish_cdp_kwargs", lambda *a, **k: None)
    monkeypatch.setattr(
        "systems.frontier_consult.prompt_expand_prelude.maybe_expand_cdp_prompt",
        lambda **kwargs: kwargs["prompt_uri"],
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate_reconcile.finalize_cdp_generate",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "agent_bus_store.db.threads_atomic.update_dispatch_link_chat_url",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_hop_cse_bind.associate_hop_seated_cse",
        _record_bind,
    )

    await worker_mod.run_cdp_worker(
        execution_id="exec-hop",
        model_id="cdp/opus-5.5",
        thread_id="child",
        caller_agent="cursor",
        prompt_uri="cortex://notes/system/ephemeral/prompt.md",
        request_id="req-hop-seat",
        mission_kind="hop",
        parent_thread="12286",
    )
    assert seen["mission_kind"] == "hop"
    assert seen["parent_thread"] == "12286"
    assert seen["thread_id"] == "child"
    assert seen["chat_url"] == _SUCCESSOR
    assert seen["registration_id"] == _REG
    assert seen["prompt_uri"] == "cortex://notes/system/ephemeral/prompt.md"


def test_hop_seated_stamp_uses_prompt_uri_not_lane_decoy_act6(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tab bind must read successor_birth_id from staged prompt, not lane Act 6."""
    from agent_bus_store.db import create_thread, get_turns, insert_turn

    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="hop-prompt-birth")
    assert detail is not None
    thread_id = str(detail["id"])
    old_birth = "a" * 32
    new_birth = "b" * 32
    insert_turn(
        thread=thread_id,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="Act 6 decoy",
        body=f"TYPE: SEAT_REGISTRATION\nsuccessor_birth_id: {old_birth}\n",
    )
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text(
        f"TYPE: CONTINUITY_HANDOFF\nsuccessor_birth_id: {new_birth}\n",
        encoding="utf-8",
    )
    prompt_uri = f"file://{prompt_path}"
    cdp_hop_cse_bind._STAMP_POSTED.clear()
    with (
        patch("agent_bus_store.db.cse_associations.associate_cse"),
        patch(
            "systems.frontier_consult.prompt_expand_prelude.read_prompt_uri",
            return_value=prompt_path.read_text(encoding="utf-8"),
        ),
    ):
        associate_hop_seated_cse(
            mission_kind="hop",
            thread_id="child",
            parent_thread=thread_id,
            chat_url=_SUCCESSOR,
            registration_id=_REG,
            execution_id="exec-prompt-birth",
            prompt_uri=prompt_uri,
        )
    turns = get_turns(thread=thread_id, last=10)
    stamp_turns = [
        t
        for t in turns
        if str(t.get("body") or "").startswith("TYPE: SEAT_REGISTRATION")
        and "source: cursor-auto" in str(t.get("body") or "")
    ]
    assert len(stamp_turns) == 1
    body = stamp_turns[0]["body"]
    assert f"successor_birth_id: {new_birth}" in body
    assert old_birth not in body
