"""Envelope serializer and budget tests."""

from __future__ import annotations

import pytest

from session_store.envelope import (
    _blocks_size,
    _build_blocks,
    _current_request_md,
    _live_turns,
    _utf8_size,
    seal,
)
from session_store.models import (
    AttachmentRef,
    Budget,
    EnvelopeOverBudget,
    InvalidRefError,
    SessionForSeal,
    Turn,
)


def _session(**overrides) -> SessionForSeal:
    base = SessionForSeal(
        session_id="owui-3f2a",
        turn_count=10,
        transcript_uri="cortex://sessions/owui-3f2a/transcript.md",
        rollup_text="User is designing a Grafana alert pipeline.",
        index_lines=["0001 user: asks how to wire Grafana alerts"],
        turns=[
            Turn(n=i, role="user" if i % 2 else "assistant", body=f"body turn {i}" * 20)
            for i in range(1, 10)
        ],
        attachments=[],
        refs=["cortex://sessions/owui-3f2a/transcript.md"],
    )
    for key, val in overrides.items():
        setattr(base, key, val)
    return base


def test_rejects_non_cortex_ref() -> None:
    with pytest.raises(InvalidRefError):
        seal(_session(refs=["workspaces://universal-llm-gateway/foo.md"]), "hello")


def test_rollup_absent_when_empty() -> None:
    result = seal(_session(rollup_text="", index_lines=[]), "current msg")
    assert "<session_rollup>" not in result.prompt_text
    assert "<session_index>" not in result.prompt_text


def test_rollup_present_with_inline_prose() -> None:
    result = seal(_session(), "current msg")
    assert "<session_rollup>" in result.prompt_text
    assert "Grafana alert pipeline" in result.prompt_text
    assert 'ref="cortex://' not in result.prompt_text.split("<session_rollup>")[1].split(
        "</session_rollup>"
    )[0]


def test_current_turn_not_in_live() -> None:
    result = seal(_session(turn_count=10), "current msg")
    live_block = result.prompt_text.split("<session_live>")[1].split("</session_live>")[0]
    assert "### Turn 0010 —" not in live_block
    assert "## Current request" in result.prompt_text
    assert "current msg" in result.prompt_text


def test_budget_ladder_shrinks_k() -> None:
    huge = _session(
        turns=[Turn(n=i, role="user", body="x" * 3000) for i in range(1, 10)],
    )
    result = seal(huge, "ok", k=6, budget=Budget(total=8000, live=4000, per_turn_cap=500))
    assert result.k_used <= 6


def test_over_budget_raises_request_byte_identical() -> None:
    huge = _session(
        rollup_text="r" * 5000,
        turns=[Turn(n=1, role="user", body="b")],
        turn_count=2,
    )
    msg = "exact request bytes must survive"
    with pytest.raises(EnvelopeOverBudget):
        seal(huge, msg, k=2, budget=Budget(total=200))
    result = seal(
        _session(rollup_text="short", turns=[Turn(n=1, role="user", body="b")], turn_count=2),
        msg,
        k=2,
    )
    assert result.prompt_text.endswith(f"{msg}\n")
    assert msg in result.prompt_text.split("## Current request\n\n")[1]


def test_attachment_ref_must_be_cortex() -> None:
    att = AttachmentRef(
        turn=1,
        name="f.json",
        ref="workspaces://repo/f.json",
        media="application/json",
    )
    with pytest.raises(InvalidRefError):
        seal(_session(attachments=[att]), "hi")


def _ladder_k_sequence(session: SessionForSeal, k_start: int, budget: Budget, msg: str) -> list[int]:
    request_md = _current_request_md(msg)
    k_used = k_start
    blocks = _build_blocks(session, k_used, budget)
    visited = [k_used]
    while _blocks_size(blocks) + _utf8_size(request_md) > budget.total and k_used > 2:
        k_used -= 1
        blocks = _build_blocks(session, k_used, budget)
        visited.append(k_used)
    return visited


def _assert_live_dedup(session: SessionForSeal, k: int) -> None:
    live = _live_turns(session, k)
    live_ns = {t.n for t in live}
    assert session.turn_count not in live_ns
    assert not live_ns.intersection({session.turn_count})


def test_budget_ladder_k_floor_never_below_two() -> None:
    session = _session(
        turn_count=10,
        turns=[Turn(n=i, role="user", body="x" * 4000) for i in range(1, 10)],
    )
    budget = Budget(total=3000, live=1500, per_turn_cap=800)
    visited = _ladder_k_sequence(session, k_start=6, budget=budget, msg="keep me whole")
    assert session.turn_count > 2
    assert min(visited) >= 2
    result = seal(session, "keep me whole", k=6, budget=budget)
    assert result.k_used >= 2


def test_budget_ladder_dedup_at_every_k() -> None:
    session = _session(
        turn_count=12,
        turns=[Turn(n=i, role="user" if i % 2 else "assistant", body=f"payload {i}" * 80) for i in range(1, 12)],
    )
    budget = Budget(total=5000, live=2500, per_turn_cap=700)
    visited = _ladder_k_sequence(session, k_start=8, budget=budget, msg="dedup probe")
    assert len(visited) >= 2
    for k in visited:
        _assert_live_dedup(session, k)


def test_budget_ladder_raises_after_k_floor_request_intact() -> None:
    session = _session(
        turn_count=10,
        rollup_text="r" * 6000,
        turns=[Turn(n=i, role="user", body="y" * 2000) for i in range(1, 10)],
    )
    msg = "exact bytes must survive hostile over-budget ladder"
    budget = Budget(total=2500, live=1200, per_turn_cap=600)
    visited = _ladder_k_sequence(session, k_start=6, budget=budget, msg=msg)
    assert min(visited) == 2
    with pytest.raises(EnvelopeOverBudget) as exc:
        seal(session, msg, k=6, budget=budget)
    assert exc.value.k_used >= 2
    ok = seal(
        _session(
            turn_count=2,
            rollup_text="short",
            turns=[Turn(n=1, role="user", body="small")],
        ),
        msg,
        k=2,
        budget=Budget(total=32000),
    )
    assert msg in ok.prompt_text.split("## Current request\n\n")[1]
    assert ok.prompt_text.endswith(f"{msg}\n")
