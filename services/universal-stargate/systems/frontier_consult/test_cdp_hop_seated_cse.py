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


def test_non_hop_child_lane_without_houseless_shape_does_not_associate(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordinary consult on a child lane must not mint associate or stamp."""
    from agent_bus_store.db import create_thread
    from agent_bus_store.db.lane_associations import associate_lane

    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    parent = create_thread(
        thread_id=None, slug="orchestration-house", tags=["role:root"]
    )
    assert parent is not None
    child = create_thread(thread_id=None, slug="mission-child")
    assert child is not None
    parent_id = str(parent["id"])
    child_id = str(child["id"])
    associate_lane(
        thread_id=child_id,
        parent_thread_id=parent_id,
        lane_role="side",
        bound_by="test",
    )
    with patch("agent_bus_store.db.cse_associations.associate_cse") as associate:
        associate_hop_seated_cse(
            mission_kind=None,
            thread_id=child_id,
            parent_thread=None,
            chat_url=_SUCCESSOR,
            registration_id=_REG,
            execution_id="f1f2f3f4-1111-4222-8333-444455556666",
            purpose="ask",
            mailbox_thread=child_id,
        )
    associate.assert_not_called()


def test_houseless_first_seat_associates_and_stamps(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parentless mailbox first birth gets mint associate + SEAT_REGISTRATION."""
    from agent_bus_store.db import create_thread, get_turns
    from agent_bus_store.db.cse_associations import HOUSELESS_FIRST_SEATED_BOUND_BY

    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="houseless-mailbox", tags=["mailbox"])
    assert detail is not None
    thread_id = str(detail["id"])
    exec_id = "f1f2f3f4-1111-4222-8333-444455556666"
    birth_hex = exec_id.replace("-", "")
    cdp_hop_cse_bind._STAMP_POSTED.clear()
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        associate_hop_seated_cse(
            mission_kind=None,
            thread_id=thread_id,
            parent_thread=None,
            chat_url=_SUCCESSOR,
            registration_id=_REG,
            execution_id=exec_id,
            purpose="houseless",
            mailbox_thread=thread_id,
        )
    from agent_bus_store.db.cse_associations import get_current_cse

    cse = get_current_cse(thread_id=thread_id)
    assert cse["cse_chat_url"] == _SUCCESSOR
    assert cse["bound_by"] == HOUSELESS_FIRST_SEATED_BOUND_BY
    turns = get_turns(thread=thread_id, last=5)
    stamp_turns = [
        t
        for t in turns
        if str(t.get("body") or "").startswith("TYPE: SEAT_REGISTRATION")
    ]
    assert len(stamp_turns) == 1
    body = stamp_turns[0]["body"]
    assert f"successor_birth_id: {birth_hex}" in body
    assert f"registration_id: {_REG}" in body
    assert f"mailbox_thread: {thread_id}" in body
    assert "parent_thread:" not in body


def test_hop_seat_registration_stamp_unchanged_golden() -> None:
    """Hop tab-bind stamp bytes must stay stable (parent_thread line)."""
    from hop_handoff.body import build_seat_registration_stamp

    stamp = build_seat_registration_stamp(
        successor_birth_id="c" * 32,
        registration_id=_REG,
        execution_id="exec-golden-hop",
        parent_thread="12286",
        chat_url=_SUCCESSOR,
        observed_at="2026-08-13T21:00:00Z",
    )
    assert stamp == (
        "TYPE: SEAT_REGISTRATION\n"
        f"successor_birth_id: {'c' * 32}\n"
        f"registration_id: {_REG}\n"
        "execution_id: exec-golden-hop\n"
        f"chat_url: {_SUCCESSOR}\n"
        "parent_thread: 12286\n"
        "observed_at: 2026-08-13T21:00:00Z\n"
        "source: cursor-auto — projection of cdp-registry active store; "
        "recovery: /v1/project-ask/active-work\n"
    )


def test_houseless_stamp_on_distinct_mailbox_not_handoff_thread(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stamp targets mailbox_thread even when generate thread_id is a handoff child."""
    from agent_bus_store.db import create_thread, get_turns

    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    mailbox = create_thread(thread_id=None, slug="seat-mailbox", tags=["mailbox"])
    handoff = create_thread(thread_id=None, slug="cdp-handoff-child")
    assert mailbox and handoff
    mailbox_id = str(mailbox["id"])
    handoff_id = str(handoff["id"])
    cdp_hop_cse_bind._STAMP_POSTED.clear()
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        associate_hop_seated_cse(
            mission_kind=None,
            thread_id=handoff_id,
            parent_thread=None,
            chat_url=_SUCCESSOR,
            registration_id=_REG,
            execution_id="a1a2a3a4-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            purpose="houseless",
            mailbox_thread=mailbox_id,
        )
    turns_mailbox = get_turns(thread=mailbox_id, last=5)
    turns_handoff = get_turns(thread=handoff_id, last=5)
    assert any(
        str(t.get("body") or "").startswith("TYPE: SEAT_REGISTRATION")
        for t in turns_mailbox
    )
    assert not any(
        str(t.get("body") or "").startswith("TYPE: SEAT_REGISTRATION")
        for t in turns_handoff
    )


def test_parentless_thread_without_mailbox_tag_does_not_associate(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_bus_store.db import create_thread

    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="not-tagged-mailbox")
    assert detail is not None
    thread_id = str(detail["id"])
    with patch("agent_bus_store.db.cse_associations.associate_cse") as associate:
        associate_hop_seated_cse(
            mission_kind=None,
            thread_id=thread_id,
            parent_thread=None,
            chat_url=_SUCCESSOR,
            registration_id=_REG,
            execution_id="f1f2f3f4-1111-4222-8333-444455556666",
            purpose="houseless",
        )
    associate.assert_not_called()


def test_seated_houseless_does_not_rebind_on_non_houseless_consult(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later purpose=ask generate must not hijack the mailbox CSE."""
    from agent_bus_store.db import create_thread, get_turns
    from agent_bus_store.db.cse_associations import (
        HOUSELESS_FIRST_SEATED_BOUND_BY,
        get_current_cse,
    )

    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="seated-mailbox", tags=["mailbox"])
    assert detail is not None
    thread_id = str(detail["id"])
    other_url = "https://claude.ai/cowork/cse_01OTHERSEATXXXXXXXXXXXX"
    cdp_hop_cse_bind._STAMP_POSTED.clear()
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        associate_hop_seated_cse(
            mission_kind=None,
            thread_id=thread_id,
            parent_thread=None,
            chat_url=_SUCCESSOR,
            registration_id=_REG,
            execution_id="a1a2a3a4-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            purpose="houseless",
            mailbox_thread=thread_id,
        )
    with patch("agent_bus_store.db.cse_associations.associate_cse") as associate:
        associate_hop_seated_cse(
            mission_kind=None,
            thread_id=thread_id,
            parent_thread=None,
            chat_url=other_url,
            registration_id="other-reg-id",
            execution_id="b2b3b4b5-cccc-4ddd-9eee-ffffffffffff",
            purpose="ask",
            mailbox_thread=thread_id,
        )
    associate.assert_not_called()
    cse = get_current_cse(thread_id=thread_id)
    assert cse["cse_chat_url"] == _SUCCESSOR
    assert cse["bound_by"] == HOUSELESS_FIRST_SEATED_BOUND_BY
    turns = get_turns(thread=thread_id, last=10)
    assert sum(
        1 for t in turns if str(t.get("body") or "").startswith("TYPE: SEAT_REGISTRATION")
    ) == 1


_U2_HOP_AFTER_HOUSELESS = (
    "https://claude.ai/cowork/cse_01HopAfterHouselessXXXXXXXXXXXX"
)


def test_hop_on_houseless_mailbox_replaces_first_seat_url(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Houseless first seat on mailbox M at U1, hop seat on M at U2 → current is U2.

    Falsifies B4 when houseless mint hold blocks ``HOP_SEATED_BOUND_BY``.
    """
    from agent_bus_store.db import create_thread
    from agent_bus_store.db.cse_associations import (
        HOP_SEATED_BOUND_BY,
        get_current_cse,
    )

    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="hl-then-hop-mailbox", tags=["mailbox"])
    assert detail is not None
    mailbox_id = str(detail["id"])
    cdp_hop_cse_bind._STAMP_POSTED.clear()
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        associate_hop_seated_cse(
            mission_kind=None,
            thread_id=mailbox_id,
            parent_thread=None,
            chat_url=_SUCCESSOR,
            registration_id=_REG,
            execution_id="a1a2a3a4-bbbb-4ccc-8ddd-111111111111",
            purpose="houseless",
            mailbox_thread=mailbox_id,
        )
        associate_hop_seated_cse(
            mission_kind="hop",
            thread_id="hop-child",
            parent_thread=mailbox_id,
            chat_url=_U2_HOP_AFTER_HOUSELESS,
            registration_id="reg-hop-after-houseless",
            execution_id="b2b3b4b5-cccc-4ddd-8eee-222222222222",
        )
    cse = get_current_cse(thread_id=mailbox_id)
    assert cse["cse_chat_url"] == _U2_HOP_AFTER_HOUSELESS
    assert cse["bound_by"] == HOP_SEATED_BOUND_BY


def test_houseless_seated_callback_is_idempotent(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second seated callback for the same triple must not double-stamp."""
    from agent_bus_store.db import create_thread, get_turns

    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="idempotent-mailbox", tags=["mailbox"])
    assert detail is not None
    thread_id = str(detail["id"])
    exec_id = "a1a2a3a4-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    cdp_hop_cse_bind._STAMP_POSTED.clear()
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        for _ in range(2):
            associate_hop_seated_cse(
                mission_kind=None,
                thread_id=thread_id,
                parent_thread=None,
                chat_url=_SUCCESSOR,
                registration_id=_REG,
                execution_id=exec_id,
                purpose="houseless",
                mailbox_thread=thread_id,
            )
    turns = get_turns(thread=thread_id, last=10)
    stamp_turns = [
        t
        for t in turns
        if str(t.get("body") or "").startswith("TYPE: SEAT_REGISTRATION")
    ]
    assert len(stamp_turns) == 1


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
async def test_worker_seated_hook_passes_houseless_mailbox_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        execution_id="exec-hl",
        model_id="cdp/opus-5.5",
        thread_id="99901",
        caller_agent="seat-mailbox",
        prompt_uri="cortex://notes/system/ephemeral/prompt.md",
        request_id="req-hl",
        purpose="houseless",
        mailbox_thread="22760",
    )
    assert seen["purpose"] == "houseless"
    assert seen["mailbox_thread"] == "22760"


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
    assert seen.get("mailbox_thread") is None
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


def test_hop_seated_stamp_uses_hop_request_birth_over_standing_handoff_render(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Column-0 birth in CURRENT LEG must not win over hop request data."""
    from agent_bus_store.db import create_thread, get_turns
    from claude_bundles.operator_proxy_mission import (
        ensure_operator_proxy_mission_prompt,
    )

    def _noop_fetch(_lane: str) -> list[object]:
        return []

    old_birth = "a" * 32
    new_birth = "b" * 32
    sidecar = (
        f"## CURRENT LEG\nsuccessor_birth_id: {old_birth}\nLEG 1\n### OWED / NEXT\nok\n"
    )
    hop_body = (
        f"TYPE: CONTINUITY_HANDOFF\nthread_id: 12286\nsuccessor_birth_id: {new_birth}\n"
    )
    rendered = ensure_operator_proxy_mission_prompt(
        hop_body,
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    before_hop_data, _after = rendered.split("## Hop request (data)", 1)
    assert f"successor_birth_id: {old_birth}" in before_hop_data
    assert f"successor_birth_id: {new_birth}" in rendered

    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="hop-render-birth")
    assert detail is not None
    thread_id = str(detail["id"])
    prompt_path = tmp_path / "mission.md"
    prompt_path.write_text(rendered, encoding="utf-8")
    prompt_uri = f"file://{prompt_path}"
    cdp_hop_cse_bind._STAMP_POSTED.clear()
    with (
        patch("agent_bus_store.db.cse_associations.associate_cse"),
        patch(
            "systems.frontier_consult.prompt_expand_prelude.read_prompt_uri",
            return_value=rendered,
        ),
    ):
        associate_hop_seated_cse(
            mission_kind="hop",
            thread_id="child",
            parent_thread=thread_id,
            chat_url=_SUCCESSOR,
            registration_id=_REG,
            execution_id="exec-render-birth",
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
