"""AC-bus-scoping: per-dispatch recipient isolation tests.

These tests verify that cursor-sdk generate dispatches scope the bus turn
``to_agent`` to ``cursor-sdk:dispatch:{execution_id}``, so that
``fetch-unread --to cursor-sdk`` returns EMPTY regardless of dispatch count.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock

import pytest
from agent_bus_store.db import (
    create_thread_with_turn,
    init_db,
)
from agent_bus_store.db.turns import get_turns

from systems.frontier_consult.cursor_sdk_generate import dispatch_cursor_sdk_generate

_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


@pytest.fixture(autouse=True)
def _bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()


def _common_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch signal emitters and worker dispatch — not under test here."""
    for name in (
        "emit_sdk_generate_requested",
        "emit_sdk_thread_created",
        "emit_sdk_worker_outcome",
    ):
        monkeypatch.setattr(
            f"systems.frontier_consult.cursor_sdk_generate.{name}",
            lambda **_kw: None,
        )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.admit_handoff_dispatch",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker_message",
        AsyncMock(return_value=(True, None)),
    )


@pytest.mark.asyncio
async def test_new_thread_recipient_scoped_to_execution_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: new-thread path — pointer to == cursor-sdk:dispatch:{execution_id}.
    AC4: thread tag_agent passed as 'cursor-sdk' (family-level).
    """
    _common_stubs(monkeypatch)
    captured: list[dict] = []

    async def _capture_create(**kwargs):
        captured.append(kwargs)
        return "thread-new-scoped"

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.create_handoff_thread",
        _capture_create,
    )

    result = await dispatch_cursor_sdk_generate(
        request_id="req-scope-new",
        role="cursor-sdk",
        contract="light-bounded",
        model=None,
        subject="scoping test new",
        caller_agent="claude-web",
        packet_path=None,
        message_text="task payload",
        reuse_thread=None,
    )

    assert len(captured) == 1, "create_handoff_thread must be called exactly once"
    execution_id = result["execution_id"]
    assert _UUID4_RE.match(execution_id), f"not a uuid4: {execution_id!r}"
    assert captured[0]["to_agent"] == f"cursor-sdk:dispatch:{execution_id}", (
        f"pointer to_agent mismatch: {captured[0]['to_agent']!r}"
    )
    # AC4: tag stays family-level
    assert captured[0].get("tag_agent") == "cursor-sdk", (
        f"expected tag_agent='cursor-sdk', got {captured[0].get('tag_agent')!r}"
    )


@pytest.mark.asyncio
async def test_reuse_thread_recipient_scoped_to_execution_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: reuse_thread path — pointer to == cursor-sdk:dispatch:{execution_id}."""
    _common_stubs(monkeypatch)
    post_kwargs: list[dict] = []

    async def _capture_post(**kwargs):
        post_kwargs.append(kwargs)

    monkeypatch.setattr(
        "systems.frontier_consult.handoff.post_pointer_turn",
        _capture_post,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.create_handoff_thread",
        AsyncMock(),
    )

    result = await dispatch_cursor_sdk_generate(
        request_id="req-scope-reuse",
        role="cursor-sdk",
        contract="light-bounded",
        model=None,
        subject="scoping test reuse",
        caller_agent="claude-web",
        packet_path=None,
        message_text="task payload",
        reuse_thread="existing-thread-999",
    )

    assert len(post_kwargs) == 1, "post_pointer_turn must be called exactly once"
    execution_id = result["execution_id"]
    assert post_kwargs[0]["to_agent"] == f"cursor-sdk:dispatch:{execution_id}", (
        f"reuse pointer to_agent mismatch: {post_kwargs[0]['to_agent']!r}"
    )


@pytest.mark.asyncio
async def test_two_dispatch_bus_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC3+AC6: Two dispatches → distinct scoped recipients → inbox empty.

    Each pointer turn is stored in the in-process bus DB addressed to
    ``cursor-sdk:dispatch:{execution_id}``.  Querying the inbox for the shared
    family slug ``cursor-sdk`` must return an empty set — the per-dispatch
    scoping makes contamination structurally impossible.
    """
    _common_stubs(monkeypatch)
    captured_recipients: list[str] = []

    async def _create_and_insert(**kwargs):
        recipient = kwargs["to_agent"]
        captured_recipients.append(recipient)
        # Insert into the in-process SQLite bus DB so we can query it below.
        thread_row, *_ = create_thread_with_turn(
            slug=f"sdk-iso-{len(captured_recipients)}",
            from_agent="dispatch",
            to_agent=recipient,
            subject=kwargs.get("subject", "pointer"),
            body="sdk dispatch pointer",
        )
        return str(thread_row["id"])

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.create_handoff_thread",
        _create_and_insert,
    )

    result_a = await dispatch_cursor_sdk_generate(
        request_id="req-iso-a",
        role="cursor-sdk",
        contract="light-bounded",
        model=None,
        subject="dispatch-a",
        caller_agent="claude-web",
        packet_path=None,
        message_text="task A",
    )
    result_b = await dispatch_cursor_sdk_generate(
        request_id="req-iso-b",
        role="cursor-sdk",
        contract="light-bounded",
        model=None,
        subject="dispatch-b",
        caller_agent="claude-web",
        packet_path=None,
        message_text="task B",
    )

    exec_a = result_a["execution_id"]
    exec_b = result_b["execution_id"]

    # Distinct execution_ids → distinct per-dispatch recipients.
    assert exec_a != exec_b, "execution_ids must be different across dispatches"
    assert captured_recipients[0] == f"cursor-sdk:dispatch:{exec_a}"
    assert captured_recipients[1] == f"cursor-sdk:dispatch:{exec_b}"

    # AC3: the shared family inbox must be empty — turns are NOT addressed there.
    shared_inbox = get_turns(to="cursor-sdk", unread=True)
    assert shared_inbox == [], (
        f"fetch-unread --to cursor-sdk must return ZERO; "
        f"got {len(shared_inbox)} turn(s): {[t['to_agent'] for t in shared_inbox]}"
    )
