"""Commit apply path — kind binding, double-commit, resume, exactly-one side effects."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from life_intent.commit import apply_commit
from life_intent.proposal_store import (
    PROPOSAL_KIND_LIFE_INTENT,
    PROPOSAL_TTL_SECONDS,
    clear_store,
    create_proposal,
    get_proposal,
    seed_proposal,
)

_FIX_INTENT = {
    "verb": "fix",
    "subject": "login timeout",
    "detail": "Users see timeout after thirty seconds consistently.",
    "urgency": "normal",
}


@dataclass(frozen=True)
class _FakeHandle:
    request_id: str = "req1"
    execution_id: str = "exec1"
    dispatch_id: str = "req1-aaaaaaaa"
    thread_id: str = "thread-1"
    resolved_model: str = "cursor/grok-4.6"
    role: str = "cursor-sdk"
    family: str = "cursor"
    platform: str = "sdk"
    to_agent: str = "cursor-sdk:dispatch:exec1"
    handoff_contract: str = "light-bounded"
    packet_path: str = "packet.md"
    message: str | None = None
    caller_agent: str = "web-anthropic"
    read_only: bool = False
    aligned_knobs: dict | None = None
    prompt_preamble: str | None = None
    thread_subject: str = "login timeout"
    pointer_body: str = "ptr"
    effective_bus_lifecycle: str = "persistent"
    parent_dispatch_thread_id: str | None = None
    dispatch_thread_id: str | None = None
    density_triage: str | None = None
    review_opt_out_reason_code: str | None = None
    auto_review_child: bool = False
    auto_review_defaulted: bool = False
    claimed_via_atomic: bool = False
    admitted: bool = True
    alignment_warnings: tuple = ()
    knob_resolution: tuple = ()


def _seed_open_proposal() -> str:
    return create_proposal(
        normalized_intent=_FIX_INTENT,
        work_order="work item",
        verb="fix",
        lane="bug_recon",
    )


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    clear_store()


@pytest.fixture
def _live_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIFE_INTENT_COMMIT_LIVE", "1")
    monkeypatch.setattr(
        "life_intent.commit._write_packet", lambda _text, _slug: "packet.md"
    )
    monkeypatch.setattr(
        "life_intent.commit._ensure_entity",
        lambda _seed: "todo:life-intent-login-timeout",
    )
    monkeypatch.setattr("life_intent.commit._create_context_edge", lambda *_args: None)
    monkeypatch.setattr(
        "life_intent.commit._prepare_recon_handle",
        AsyncMock(return_value=_FakeHandle()),
    )
    monkeypatch.setattr(
        "life_intent.commit._submit_prepared_handle",
        AsyncMock(return_value="thread-1"),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_prepare.handle_to_dict",
        lambda handle: {
            "request_id": handle.request_id,
            "execution_id": handle.execution_id,
            "dispatch_id": handle.dispatch_id,
            "thread_id": handle.thread_id,
            "resolved_model": handle.resolved_model,
            "role": handle.role,
            "family": handle.family,
            "platform": handle.platform,
            "to_agent": handle.to_agent,
            "handoff_contract": handle.handoff_contract,
            "packet_path": handle.packet_path,
            "message": handle.message,
            "caller_agent": handle.caller_agent,
            "read_only": handle.read_only,
            "aligned_knobs": handle.aligned_knobs,
            "prompt_preamble": handle.prompt_preamble,
            "thread_subject": handle.thread_subject,
            "pointer_body": handle.pointer_body,
            "effective_bus_lifecycle": handle.effective_bus_lifecycle,
            "parent_dispatch_thread_id": handle.parent_dispatch_thread_id,
            "dispatch_thread_id": handle.dispatch_thread_id,
            "density_triage": handle.density_triage,
            "review_opt_out_reason_code": handle.review_opt_out_reason_code,
            "auto_review_child": handle.auto_review_child,
            "auto_review_defaulted": handle.auto_review_defaulted,
            "claimed_via_atomic": handle.claimed_via_atomic,
            "admitted": handle.admitted,
            "alignment_warnings": list(handle.alignment_warnings),
            "knob_resolution": list(handle.knob_resolution),
        },
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_prepare.handle_from_dict",
        lambda data: _FakeHandle(
            request_id=str(data["request_id"]),
            execution_id=str(data["execution_id"]),
            dispatch_id=str(data["dispatch_id"]),
            thread_id=str(data["thread_id"]),
        ),
    )


def test_commit_rejects_foreign_proposal_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIFE_INTENT_COMMIT_LIVE", "1")
    imprint_id = "00000000-0000-4000-8000-000000000001"
    outcome = asyncio.run(apply_commit(imprint_id))
    assert outcome.code == "foreign_proposal_kind"


def test_commit_rejects_wrong_kind_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIFE_INTENT_COMMIT_LIVE", "1")
    proposal_id = "00000000-0000-4000-8000-000000000002"
    seed_proposal(
        proposal_id=proposal_id,
        normalized_intent=_FIX_INTENT,
        kind="cortex.life/v1",
    )
    outcome = asyncio.run(apply_commit(proposal_id))
    assert outcome.code == "foreign_proposal_kind"


def test_stored_proposal_binds_life_intent_kind() -> None:
    proposal_id = _seed_open_proposal()
    row = get_proposal(proposal_id)
    assert row is not None
    assert row.kind == PROPOSAL_KIND_LIFE_INTENT


def test_sequential_double_commit_one_success_one_reject(
    monkeypatch: pytest.MonkeyPatch, _live_commit: None
) -> None:
    entity_calls: list[object] = []
    submit_calls: list[object] = []

    monkeypatch.setattr(
        "life_intent.commit._ensure_entity",
        lambda seed: entity_calls.append(seed) or "todo:life-intent-login-timeout",
    )
    monkeypatch.setattr(
        "life_intent.commit._submit_prepared_handle",
        AsyncMock(side_effect=lambda handle: submit_calls.append(handle) or "thread-1"),
    )

    proposal_id = _seed_open_proposal()
    first = asyncio.run(apply_commit(proposal_id))
    second = asyncio.run(apply_commit(proposal_id))

    assert first.committed is True
    assert second.code == "proposal_already_committed"
    assert len(entity_calls) == 1
    assert len(submit_calls) == 1
    row = get_proposal(proposal_id)
    assert row is not None
    assert row.status == "completed"


def test_concurrent_double_commit_exactly_one_winner(
    monkeypatch: pytest.MonkeyPatch, _live_commit: None
) -> None:
    entity_calls: list[object] = []
    submit_calls: list[object] = []
    barrier = threading.Barrier(2)
    results: list[object] = []

    monkeypatch.setattr(
        "life_intent.commit._ensure_entity",
        lambda seed: entity_calls.append(seed) or "todo:life-intent-login-timeout",
    )

    async def _submit(handle: object) -> str:
        submit_calls.append(handle)
        return "thread-1"

    monkeypatch.setattr("life_intent.commit._submit_prepared_handle", _submit)

    proposal_id = _seed_open_proposal()

    def _attempt() -> None:
        barrier.wait()
        results.append(asyncio.run(apply_commit(proposal_id)))

    threads = [threading.Thread(target=_attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    committed = [r for r in results if getattr(r, "committed", False)]
    rejected = [
        r for r in results if getattr(r, "code", None) == "proposal_already_committed"
    ]
    assert len(committed) == 1
    assert len(rejected) == 1
    assert len(entity_calls) == 1
    assert len(submit_calls) == 1


def test_packet_write_failure_leaves_failed_then_resume_completes(
    monkeypatch: pytest.MonkeyPatch, _live_commit: None
) -> None:
    writes = {"n": 0}
    entity_calls: list[object] = []
    submit_calls: list[object] = []

    def _write(_text: str, _slug: str) -> str:
        writes["n"] += 1
        if writes["n"] == 1:
            raise RuntimeError("disk full")
        return "packet.md"

    monkeypatch.setattr("life_intent.commit._write_packet", _write)
    monkeypatch.setattr(
        "life_intent.commit._ensure_entity",
        lambda seed: entity_calls.append(seed) or "todo:life-intent-login-timeout",
    )
    monkeypatch.setattr(
        "life_intent.commit._submit_prepared_handle",
        AsyncMock(side_effect=lambda handle: submit_calls.append(handle) or "thread-1"),
    )

    proposal_id = _seed_open_proposal()
    first = asyncio.run(apply_commit(proposal_id))
    assert first.code == "commit_incomplete"
    assert get_proposal(proposal_id).status == "failed"
    assert entity_calls == []
    assert submit_calls == []

    second = asyncio.run(apply_commit(proposal_id))
    assert second.committed is True
    assert len(entity_calls) == 1
    assert len(submit_calls) == 1
    assert get_proposal(proposal_id).status == "completed"


def test_entity_create_failure_leaves_failed_then_resume_completes(
    monkeypatch: pytest.MonkeyPatch, _live_commit: None
) -> None:
    entity_calls: list[object] = []
    submit_calls: list[object] = []

    def _ensure(seed: object) -> str:
        entity_calls.append(seed)
        if len(entity_calls) == 1:
            raise RuntimeError("cortex down")
        return "todo:life-intent-login-timeout"

    monkeypatch.setattr("life_intent.commit._ensure_entity", _ensure)
    monkeypatch.setattr(
        "life_intent.commit._submit_prepared_handle",
        AsyncMock(side_effect=lambda handle: submit_calls.append(handle) or "thread-1"),
    )

    proposal_id = _seed_open_proposal()
    first = asyncio.run(apply_commit(proposal_id))
    assert first.code == "commit_incomplete"
    assert get_proposal(proposal_id).status == "failed"
    assert get_proposal(proposal_id).packet_path == "packet.md"
    assert submit_calls == []

    second = asyncio.run(apply_commit(proposal_id))
    assert second.committed is True
    assert len(entity_calls) == 2  # first fail + second success (no entity_id yet)
    # Wait - on resume entity_id is None still because record_entity wasn't called
    # after failure. So ensure is called again - that's correct. After success
    # entity_id is recorded. Across lifecycle at most one successful create.
    assert len(submit_calls) == 1


def test_dispatch_failure_leaves_failed_then_resume_completes(
    monkeypatch: pytest.MonkeyPatch, _live_commit: None
) -> None:
    submit_calls: list[object] = []

    async def _submit(handle: object) -> str:
        submit_calls.append(handle)
        if len(submit_calls) == 1:
            raise RuntimeError("worker 500")
        return "thread-1"

    monkeypatch.setattr("life_intent.commit._submit_prepared_handle", _submit)

    proposal_id = _seed_open_proposal()
    first = asyncio.run(apply_commit(proposal_id))
    assert first.code == "commit_incomplete"
    row = get_proposal(proposal_id)
    assert row is not None
    assert row.status == "failed"
    assert row.entity_id == "todo:life-intent-login-timeout"
    assert row.dispatch_handle is not None

    second = asyncio.run(apply_commit(proposal_id))
    assert second.committed is True
    assert len(submit_calls) == 2
    assert get_proposal(proposal_id).status == "completed"


def test_resume_does_not_refire_dispatch(
    monkeypatch: pytest.MonkeyPatch, _live_commit: None
) -> None:
    submit = AsyncMock(return_value="thread-1")
    monkeypatch.setattr("life_intent.commit._submit_prepared_handle", submit)

    proposal_id = _seed_open_proposal()
    seed_proposal(
        proposal_id=proposal_id,
        normalized_intent=_FIX_INTENT,
        status="failed",
        packet_path="packet.md",
        entity_id="todo:life-intent-login-timeout",
        dispatch_ref="thread-1",
        dispatch_handle={
            "request_id": "r",
            "execution_id": "e",
            "dispatch_id": "r-aa",
            "thread_id": "thread-1",
            "resolved_model": "m",
            "role": "cursor-sdk",
            "family": "cursor",
            "platform": "sdk",
            "to_agent": "t",
            "handoff_contract": "light-bounded",
            "packet_path": "packet.md",
            "message": None,
            "caller_agent": "web-anthropic",
            "read_only": False,
            "aligned_knobs": None,
            "prompt_preamble": None,
            "thread_subject": "s",
            "pointer_body": "p",
            "effective_bus_lifecycle": "persistent",
            "parent_dispatch_thread_id": None,
            "dispatch_thread_id": None,
            "density_triage": None,
            "review_opt_out_reason_code": None,
            "auto_review_child": False,
            "auto_review_defaulted": False,
            "claimed_via_atomic": False,
            "admitted": True,
            "alignment_warnings": [],
            "knob_resolution": [],
        },
        reply_thread="agent-bus:life-intent-login-timeout",
    )
    outcome = asyncio.run(apply_commit(proposal_id))
    assert outcome.committed is True
    submit.assert_not_awaited()


def test_failed_proposal_expired_rejects(
    monkeypatch: pytest.MonkeyPatch, _live_commit: None
) -> None:
    from life_intent.proposal_store import force_expires_at

    proposal_id = "00000000-0000-4000-8000-000000000099"
    seed_proposal(
        proposal_id=proposal_id,
        normalized_intent=_FIX_INTENT,
        status="failed",
    )
    force_expires_at(proposal_id, datetime.now(UTC) - timedelta(seconds=1))
    outcome = asyncio.run(apply_commit(proposal_id))
    assert outcome.code == "proposal_expired"


def test_purge_spares_applying() -> None:
    from life_intent.proposal_store import force_expires_at

    pid_applying = "aaaaaaaa-0000-4000-8000-000000000001"
    pid_failed = "bbbbbbbb-0000-4000-8000-000000000002"
    seed_proposal(
        proposal_id=pid_applying, normalized_intent=_FIX_INTENT, status="applying"
    )
    seed_proposal(
        proposal_id=pid_failed, normalized_intent=_FIX_INTENT, status="failed"
    )
    past = datetime.now(UTC) - timedelta(seconds=PROPOSAL_TTL_SECONDS + 10)
    force_expires_at(pid_applying, past)
    force_expires_at(pid_failed, past)
    assert get_proposal(pid_applying) is not None
    assert get_proposal(pid_failed) is None
