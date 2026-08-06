"""Hermetic tests for activity-aware chat reply wait (friction 24666)."""

from __future__ import annotations

import asyncio
import time

import pytest

from claude_bundles.chat_reply_wait import (
    HarvestIncomplete,
    _cowork_complete_enough,
    _in_flight,
    _is_user_prompt_echo,
    wait_assistant_reply,
)

pytestmark = pytest.mark.offline


def _state(
    *,
    body_len: int = 0,
    n: int = 0,
    streaming: bool = False,
    stop: bool = False,
    tool_pause: bool = False,
    error_banner: bool = False,
    error_banner_match: str = "",
    error_banner_text: str = "",
    body: str = "",
) -> dict:
    return {
        "url": "https://claude.ai/new",
        "body": body or ("x" * body_len),
        "body_len": body_len,
        "n": n,
        "streaming": streaming,
        "stop": stop,
        "error_banner": error_banner,
        "error_banner_match": error_banner_match
        or ("Overloaded" if error_banner else ""),
        "error_banner_text": error_banner_text
        or ("Failed to sample: Overloaded" if error_banner else ""),
        "tool_pause": tool_pause,
        "model_label": "Fable",
    }


class _FakePage:
    """Returns sequenced harvest dicts from page.evaluate."""

    def __init__(self, sequence: list[dict]) -> None:
        self._seq = list(sequence)
        self._i = 0

    async def evaluate(self, _js, _arg=None):  # noqa: ANN001
        if self._i < len(self._seq):
            state = self._seq[self._i]
            self._i += 1
            return state
        return self._seq[-1]


@pytest.fixture
def advance_clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    """Advance monotonic time with asyncio.sleep so idle deadlines fire."""
    clock = {"t": 1_000.0}

    def _mono() -> float:
        return clock["t"]

    async def _sleep(seconds: float) -> None:
        clock["t"] += float(seconds)

    monkeypatch.setattr(time, "monotonic", _mono)
    monkeypatch.setattr(asyncio, "sleep", _sleep)
    return clock


def test_in_flight_detects_stop_streaming_tool_pause() -> None:
    assert _in_flight(_state(stop=True))
    assert _in_flight(_state(streaming=True))
    assert _in_flight(_state(tool_pause=True))
    assert not _in_flight(_state())


@pytest.mark.asyncio
async def test_inflight_past_idle_timeout_still_completes(advance_clock) -> None:
    """Stop present longer than timeout_s must not raise — idle clock pauses."""
    before = _state(body_len=0, n=0)
    # 20 × 0.5s = 10s wall while Stop is up (timeout_s=2) then stable complete.
    inflight = [_state(body_len=50, n=0, stop=True) for _ in range(20)]
    done = [
        _state(body_len=500, n=1, body="final answer " * 40),
        _state(body_len=500, n=1, body="final answer " * 40),
        _state(body_len=500, n=1, body="final answer " * 40),
    ]
    page = _FakePage(inflight + done)
    state = await wait_assistant_reply(
        page,
        before=before,
        timeout_s=2,
        poll_ms=500,
        min_growth=50,
        min_body=200,
        stable_polls=2,
    )
    assert state["n"] == 1
    assert state["body_len"] == 500
    assert advance_clock["t"] > 1_010.0


@pytest.mark.asyncio
async def test_idle_without_growth_raises(advance_clock) -> None:
    before = _state(body_len=0, n=0)
    page = _FakePage([_state(body_len=0, n=0) for _ in range(20)])
    with pytest.raises(HarvestIncomplete, match="timed out incomplete"):
        await wait_assistant_reply(
            page,
            before=before,
            timeout_s=1,
            poll_ms=500,
            min_growth=50,
            min_body=40,
            stable_polls=2,
        )


@pytest.mark.asyncio
async def test_error_banner_idle_raises_on_timeout_with_match(advance_clock) -> None:
    """Idle+banner waits the idle budget then fails with matched phrase (25654)."""
    before = _state(body_len=0, n=0)
    page = _FakePage(
        [
            _state(
                error_banner=True,
                body_len=10,
                n=0,
                error_banner_match="Overloaded",
                error_banner_text="Failed to sample: Overloaded",
            )
            for _ in range(20)
        ]
    )
    with pytest.raises(HarvestIncomplete, match=r"error_banner.*Overloaded"):
        await wait_assistant_reply(
            page,
            before=before,
            timeout_s=1,
            poll_ms=500,
            min_growth=10,
            min_body=20,
        )


@pytest.mark.asyncio
async def test_error_banner_while_streaming_waits_then_completes(advance_clock) -> None:
    """Overloaded + Stop/streaming must not abort — wait until turn completes."""
    before = _state(body_len=0, n=0)
    inflight = [
        _state(
            body_len=50,
            n=0,
            stop=True,
            streaming=True,
            error_banner=True,
            error_banner_match="Overloaded",
        )
        for _ in range(10)
    ]
    done = [
        _state(body_len=500, n=1, body="final answer " * 40),
        _state(body_len=500, n=1, body="final answer " * 40),
        _state(body_len=500, n=1, body="final answer " * 40),
    ]
    page = _FakePage(inflight + done)
    state = await wait_assistant_reply(
        page,
        before=before,
        timeout_s=2,
        poll_ms=500,
        min_growth=50,
        min_body=200,
        stable_polls=2,
    )
    assert state["n"] == 1
    assert state["body_len"] == 500
    assert not state.get("error_banner")


@pytest.mark.asyncio
async def test_lingering_overloaded_after_turn_completes(advance_clock) -> None:
    """Lingering Overloaded after body landed must complete (friction 25684)."""
    before = _state(body_len=0, n=0)
    done = [
        _state(
            body_len=3078,
            n=1,
            body="x" * 3078,
            error_banner=True,
            error_banner_match="Overloaded",
            error_banner_text="Failed to sample: Overloaded",
        )
        for _ in range(5)
    ]
    page = _FakePage(done)
    state = await wait_assistant_reply(
        page,
        before=before,
        timeout_s=600,
        poll_ms=500,
        min_growth=50,
        min_body=200,
        stable_polls=2,
    )
    assert state["n"] == 1
    assert state["body_len"] == 3078
    assert state.get("error_banner") is True
    assert advance_clock["t"] < 1_010.0


@pytest.mark.asyncio
async def test_lingering_overloaded_complete_beats_timeout_raise(
    advance_clock,
) -> None:
    """On idle timeout, structural complete + banner returns — ¬ HarvestIncomplete."""
    before = _state(body_len=0, n=0)
    # Alternate lengths so stable never reaches stable_polls mid-loop; force
    # the timeout exit path while remaining structurally complete.
    seq = []
    for i in range(20):
        seq.append(
            _state(
                body_len=3000 + (i % 2),
                n=1,
                body="x" * (3000 + (i % 2)),
                error_banner=True,
                error_banner_match="Overloaded",
            )
        )
    page = _FakePage(seq)
    state = await wait_assistant_reply(
        page,
        before=before,
        timeout_s=1,
        poll_ms=500,
        min_growth=50,
        min_body=200,
        stable_polls=99,
    )
    assert state["n"] == 1
    assert state.get("error_banner") is True


@pytest.mark.asyncio
async def test_short_idle_reply_completes_without_length_gate(advance_clock) -> None:
    """Short assistant reply completes when turn count grew — no min_body wait."""
    before = _state(body_len=0, n=0)
    short = "FALSIFIER_OK"
    done = [
        _state(body_len=len(short), n=1, stop=False, body=short),
        _state(body_len=len(short), n=1, stop=False, body=short),
        _state(body_len=len(short), n=1, stop=False, body=short),
    ]
    page = _FakePage(done)
    state = await wait_assistant_reply(
        page,
        before=before,
        timeout_s=600,
        poll_ms=500,
        min_growth=200,
        min_body=200,
        stable_polls=2,
        min_msg_chars=5,
    )
    assert state["n"] == 1
    assert state["body_len"] == len(short)
    assert advance_clock["t"] < 1_010.0


@pytest.mark.asyncio
async def test_idle_complete_with_stop_false_succeeds(advance_clock) -> None:
    """Idle page with stop=false completes when body grew (Python wait loop)."""
    before = _state(body_len=0, n=0)
    done = [
        _state(body_len=500, n=1, stop=False, body="final answer " * 40),
        _state(body_len=500, n=1, stop=False, body="final answer " * 40),
        _state(body_len=500, n=1, stop=False, body="final answer " * 40),
    ]
    page = _FakePage(done)
    state = await wait_assistant_reply(
        page,
        before=before,
        timeout_s=2,
        poll_ms=500,
        min_growth=50,
        min_body=200,
        stable_polls=2,
    )
    assert state["n"] == 1
    assert state["body_len"] == 500


@pytest.mark.asyncio
async def test_on_harvest_receives_each_sample(advance_clock) -> None:
    """Held-page ladder hook sees every harvest (friction 25671)."""
    before = _state(body_len=0, n=0)
    samples: list[int] = []

    async def on_harvest(state: dict) -> None:
        samples.append(int(state["n"]))

    done = [
        _state(body_len=500, n=1, body="final answer " * 40),
        _state(body_len=500, n=1, body="final answer " * 40),
        _state(body_len=500, n=1, body="final answer " * 40),
    ]
    page = _FakePage(done)
    state = await wait_assistant_reply(
        page,
        before=before,
        timeout_s=2,
        poll_ms=500,
        min_growth=50,
        min_body=200,
        stable_polls=2,
        on_harvest=on_harvest,
    )
    assert state["n"] == 1
    assert len(samples) >= 2
    assert samples == [1] * len(samples)


def _cowork_quiet_state(
    *,
    body_len: int = 500,
    n: int = 1,
    task_map_present: bool = True,
    task_map_idle: bool = True,
    task_map_working: bool = False,
    body: str | None = None,
) -> dict:
    return {
        "url": "https://claude.ai/cowork/cse_test123",
        "body": body or ("x" * body_len),
        "body_len": body_len,
        "n": n,
        "streaming": True,
        "stop": True,
        "tool_pause": False,
        "error_banner": False,
        "task_map_present": task_map_present,
        "task_map_idle": task_map_idle,
        "task_map_working": task_map_working,
    }


@pytest.fixture
def structural_quiet_n(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cdp_ask.structural_quiet.STRUCTURAL_QUIET_SAMPLES", 5)


@pytest.mark.asyncio
async def test_structural_quiet_tier_a_returns_landed_body_after_n_quiet_samples(
    advance_clock, structural_quiet_n
) -> None:
    """Latched streaming/stop after land → Tier A completes after N quiet samples (AC3)."""
    before = _state(body_len=0, n=0)
    quiet = _cowork_quiet_state()
    # Anchor + 5 quiet streak samples + 2 stable completion polls.
    seq = [quiet] * (1 + 5 + 2)
    page = _FakePage(seq)
    state = await wait_assistant_reply(
        page,
        before=before,
        timeout_s=600,
        poll_ms=500,
        min_growth=50,
        min_body=200,
        stable_polls=2,
    )
    assert state["n"] == 1
    assert state["body_len"] == 500
    assert page._i == 8


@pytest.mark.asyncio
async def test_structural_quiet_growth_at_n_minus_one_resets_streak(
    advance_clock, structural_quiet_n
) -> None:
    """Body growth at sample N−1 resets quiet streak — no early return (AC4)."""
    before = _state(body_len=0, n=0)
    quiet = _cowork_quiet_state()
    growth = _cowork_quiet_state(body_len=501, body="x" * 501)
    # 4 quiet (streak 4), growth resets, 4 more quiet (streak 4) — never hits 5.
    seq = [quiet] * 5 + [growth] + [quiet] * 10
    page = _FakePage(seq)
    with pytest.raises(HarvestIncomplete, match="timed out incomplete"):
        await wait_assistant_reply(
            page,
            before=before,
            timeout_s=1,
            poll_ms=500,
            min_growth=50,
            min_body=200,
            stable_polls=2,
        )


@pytest.mark.asyncio
async def test_structural_quiet_tier_b_raises_harvest_incomplete_never_landed(
    advance_clock, structural_quiet_n
) -> None:
    """Never-landed wedge → Tier B idle timeout raises HarvestIncomplete (AC5)."""
    before = _state(body_len=0, n=0)
    quiet = _cowork_quiet_state(n=0, body_len=0, body="")
    seq = [quiet] * 20
    page = _FakePage(seq)
    with pytest.raises(HarvestIncomplete, match="timed out incomplete") as exc:
        await wait_assistant_reply(
            page,
            before=before,
            timeout_s=1,
            poll_ms=500,
            min_growth=50,
            min_body=40,
            stable_polls=2,
        )
    assert "last=0" in str(exc.value)
    assert "n=0" in str(exc.value)


@pytest.mark.asyncio
async def test_structural_quiet_task_map_working_vetoes_both_tiers(
    structural_quiet_n,
) -> None:
    """task_map_working=true vetoes Tier A and Tier B at any streak length (AC6)."""
    from cdp_ask.structural_quiet import StructuralQuietTracker

    tracker = StructuralQuietTracker()
    quiet = {
        "n": 1,
        "body_len": 500,
        "task_map_present": True,
        "task_map_idle": True,
        "task_map_working": False,
    }
    working = {**quiet, "task_map_working": True, "task_map_idle": False}

    for _ in range(6):
        tracker.observe(quiet)
    assert tracker.quiet_satisfied

    tracker.observe(working)
    assert tracker.streak == 0
    assert not tracker.quiet_satisfied

    for _ in range(6):
        tracker.observe(quiet)
    assert tracker.quiet_satisfied
    tracker.observe(working)
    assert not tracker.quiet_satisfied


def test_cowork_complete_enough_rejects_len_growth_without_n() -> None:
    """AC-S1-c: body grew but n unchanged must not complete."""
    state = {
        "url": "https://claude.ai/cowork/cse_018abc",
        "body_len": 500,
        "n": 1,
        "task_map_present": True,
        "task_map_idle": True,
        "task_map_working": False,
    }
    assert (
        _cowork_complete_enough(
            state,
            base_len=100,
            base_n=1,
            min_growth=50,
            min_body=200,
            saw_working=True,
        )
        is False
    )


def test_is_user_prompt_echo_rejects_you_said_belt() -> None:
    """AC-S1-d: echo belt rejects You said: chrome."""
    assert _is_user_prompt_echo("You said: /reasoning-posture\n")
    assert not _is_user_prompt_echo("Assistant analysis begins here.")
