"""Commit apply path — kind binding, double-commit, exactly-one side effects."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock

import pytest

from life_intent.commit import apply_commit
from life_intent.proposal_store import (
    PROPOSAL_KIND_LIFE_INTENT,
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
    monkeypatch.setattr("life_intent.commit._write_packet", lambda _text, _slug: "packet.md")
    monkeypatch.setattr("life_intent.commit._create_entity", lambda _seed: "todo:life-intent-login-timeout")
    monkeypatch.setattr("life_intent.commit._create_context_edge", lambda *_args: None)
    monkeypatch.setattr(
        "life_intent.commit._fire_recon_dispatch",
        AsyncMock(return_value="agent-bus:dispatch-1"),
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
    dispatch_calls: list[object] = []

    monkeypatch.setattr(
        "life_intent.commit._create_entity",
        lambda seed: entity_calls.append(seed) or "todo:life-intent-login-timeout",
    )
    monkeypatch.setattr(
        "life_intent.commit._fire_recon_dispatch",
        AsyncMock(side_effect=lambda **_kwargs: dispatch_calls.append(1) or "agent-bus:dispatch-1"),
    )

    proposal_id = _seed_open_proposal()
    first = asyncio.run(apply_commit(proposal_id))
    second = asyncio.run(apply_commit(proposal_id))

    assert first.committed is True
    assert second.code == "proposal_already_committed"
    assert len(entity_calls) == 1
    assert len(dispatch_calls) == 1
    row = get_proposal(proposal_id)
    assert row is not None
    assert row.status == "committed"


def test_concurrent_double_commit_exactly_one_winner(
    monkeypatch: pytest.MonkeyPatch, _live_commit: None
) -> None:
    entity_calls: list[object] = []
    dispatch_calls: list[object] = []
    barrier = threading.Barrier(2)
    results: list[object] = []

    monkeypatch.setattr(
        "life_intent.commit._create_entity",
        lambda seed: entity_calls.append(seed) or "todo:life-intent-login-timeout",
    )

    async def _dispatch(**_kwargs: object) -> str:
        dispatch_calls.append(1)
        return "agent-bus:dispatch-1"

    monkeypatch.setattr("life_intent.commit._fire_recon_dispatch", _dispatch)

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
    rejected = [r for r in results if getattr(r, "code", None) == "proposal_already_committed"]
    assert len(committed) == 1
    assert len(rejected) == 1
    assert len(entity_calls) == 1
    assert len(dispatch_calls) == 1
