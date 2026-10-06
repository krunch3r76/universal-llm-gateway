"""Hermetic tests for activity-aware chat reply wait (friction 24666)."""

from __future__ import annotations

import asyncio
import time

import pytest
from chat_harvest.chrome import is_prompt_echo

from claude_bundles.chat_reply_wait import HarvestIncompleteError, wait_assistant_reply
from claude_bundles.cse_idle_probe import in_flight_from_state
from claude_bundles.reply_completion import (
    complete_enough as _complete_enough,
)
from claude_bundles.reply_completion import (
    cowork_complete_enough as _cowork_complete_enough,
)

_in_flight = in_flight_from_state
_is_user_prompt_echo = is_prompt_echo

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


@pytest.mark.parametrize(
    ("streaming", "stop", "tool_pause"),
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, True, False),
        (True, False, True),
        (False, True, True),
        (True, True, True),
    ],
)
def test_in_flight_from_state_matches_legacy_triple(
    streaming: bool, stop: bool, tool_pause: bool
) -> None:
    state = {
        "streaming": streaming,
        "stop": stop,
        "tool_pause": tool_pause,
    }
    legacy = bool(streaming or stop or tool_pause)
    assert in_flight_from_state(state) is legacy


@pytest.mark.parametrize("state", [{}, {"streaming": None}, {"stop": 0}])
def test_in_flight_from_state_missing_keys_falsy(state: dict) -> None:
    assert in_flight_from_state(state) is False


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
    with pytest.raises(HarvestIncompleteError, match="timed out incomplete"):
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
async def test_timeout_with_nonzero_body_carries_partial(advance_clock) -> None:
    """a:37226 — HarvestIncompleteError must retain last scrape when last≫0."""
    before = _state(body_len=0, n=0)
    # Mid-review prose: nonzero body, no sealed verdict → require_review stays open.
    partial = (
        "Review opening for the boundary redesign.\n"
        "Still gathering evidence; no sealed VERDICT line yet. " + ("x" * 80)
    )
    page = _FakePage(
        [
            _state(
                body_len=len(partial), n=1, body=partial, streaming=False, stop=False
            )
            for _ in range(20)
        ]
    )
    with pytest.raises(HarvestIncompleteError, match="timed out incomplete") as exc:
        await wait_assistant_reply(
            page,
            before=before,
            timeout_s=1,
            poll_ms=500,
            min_growth=50,
            min_body=40,
            stable_polls=2,
            require_review_verdict=True,
        )
    assert f"last={len(partial)}" in str(exc.value)
    assert exc.value.body == partial
    assert len(exc.value.body) > 0


def test_review_verdict_gate_accepts_return_to_design() -> None:
    """a:37226 — prompt-vocab RETURN_TO_DESIGN seals purpose=review harvest."""
    body = "Findings…\n\nMerits: RETURN_TO_DESIGN\n"
    state = _state(body_len=len(body), n=1, streaming=False, stop=False, body=body)
    assert (
        _complete_enough(
            state,
            base_len=0,
            base_n=0,
            min_growth=1,
            min_body=1,
            require_review_verdict=True,
        )
        is True
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
    with pytest.raises(HarvestIncompleteError, match=r"error_banner.*Overloaded"):
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
    """On idle timeout, structural complete + banner returns — ¬ HarvestIncompleteError."""
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
    with pytest.raises(HarvestIncompleteError, match="timed out incomplete"):
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
    """Never-landed wedge → Tier B idle timeout raises HarvestIncompleteError (AC5)."""
    before = _state(body_len=0, n=0)
    quiet = _cowork_quiet_state(n=0, body_len=0, body="")
    seq = [quiet] * 20
    page = _FakePage(seq)
    with pytest.raises(HarvestIncompleteError, match="timed out incomplete") as exc:
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


def test_streaming_pause_on_tool_badge_is_not_complete() -> None:
    """A streaming pause that leaves only a tool badge is not the bus reply."""
    badge = "Loaded tools, ran a command"
    state = _state(body_len=len(badge), n=1, streaming=False, stop=False, body=badge)
    assert (
        _complete_enough(
            state,
            base_len=0,
            base_n=0,
            min_growth=1,
            min_body=1,
        )
        is False
    )
    cowork = {
        **state,
        "url": "https://claude.ai/cowork/cse_018abc",
    }
    assert (
        _cowork_complete_enough(
            cowork,
            base_len=0,
            base_n=0,
            min_growth=1,
            min_body=1,
            saw_working=True,
        )
        is False
    )


def test_review_verdict_gate_rejects_skill_induction_ack() -> None:
    """a:37156 — purpose=review must not seal on skill-load preamble prose."""
    induction = (
        "Three skills loaded. Posture state:\n\n"
        "Question: not yet pinned — no substantive request in this turn, "
        "only the load directives.\n\n"
        "Give me the actual question or task and I'll pin scope and bind one leg."
    )
    state = _state(
        body_len=len(induction),
        n=1,
        streaming=False,
        stop=False,
        body=induction,
    )
    assert (
        _complete_enough(
            state,
            base_len=0,
            base_n=0,
            min_growth=1,
            min_body=1,
            require_review_verdict=True,
        )
        is False
    )
    with_verdict = {
        **state,
        "body": induction + "\n\nVERDICT: WITHHOLD\n",
        "body_len": len(induction) + len("\n\nVERDICT: WITHHOLD\n"),
    }
    assert (
        _complete_enough(
            with_verdict,
            base_len=0,
            base_n=0,
            min_growth=1,
            min_body=1,
            require_review_verdict=True,
        )
        is True
    )


def test_review_verdict_gate_rejects_mid_tool_prose() -> None:
    """a:37034 — mid-review prose without VERDICT is not complete for reviews."""
    mid = "Next I'm reading the live-tree callees"
    state = _state(body_len=len(mid), n=1, streaming=False, stop=False, body=mid)
    assert (
        _complete_enough(
            state,
            base_len=0,
            base_n=0,
            min_growth=1,
            min_body=1,
            require_review_verdict=True,
        )
        is False
    )
    # Non-review purposes still accept substantive prose without VERDICT.
    assert (
        _complete_enough(
            state,
            base_len=0,
            base_n=0,
            min_growth=1,
            min_body=1,
            require_review_verdict=False,
        )
        is True
    )


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


def _both_complete(body: str, *, expected: bool) -> None:
    state = _state(body_len=len(body), n=1, body=body)
    kwargs = {
        "base_len": 0,
        "base_n": 0,
        "min_growth": 1,
        "min_body": 1,
        "require_review_verdict": False,
    }
    assert _complete_enough(state, **kwargs) is expected
    cowork = {**state, "url": "https://claude.ai/cowork/cse_018abc"}
    assert _cowork_complete_enough(cowork, saw_working=True, **kwargs) is expected


def test_badge_shape_does_not_complete() -> None:
    """Quiet tool-status scrapes are not the reply. Phrase-list and shape."""
    from chat_harvest.test_chrome import (
        ARCHIVE_BADGE_PREAMBLE,
        UPDATED_TASKS_PREAMBLE,
    )

    for body in (
        ARCHIVE_BADGE_PREAMBLE,
        UPDATED_TASKS_PREAMBLE,
        "Browsed files, edited a note.",
        "Edited a note\nEdited a note",
    ):
        _both_complete(body, expected=False)


def test_terse_prose_and_open_fork_still_complete() -> None:
    """Pronoun prose, Done, specimen 347, and a verdict-less consult complete."""
    from chat_harvest.test_chrome import OPEN_FORK_CONSULT, SPECIMEN_347_BODY

    for body in (
        SPECIMEN_347_BODY,
        "Fixed it, reran tests.",
        "Done.",
        OPEN_FORK_CONSULT,
    ):
        _both_complete(body, expected=True)


@pytest.mark.asyncio
async def test_badge_then_prose_returns_later_sample(advance_clock) -> None:
    """A shape-badge sample must not return before the prose sample."""
    badge = "Browsed files, edited a note."
    prose = badge + "\n\nFixed the gate and returned the later answer."
    early = [_state(body_len=len(badge), n=1, body=badge) for _ in range(3)]
    done = [_state(body_len=len(prose), n=2, body=prose) for _ in range(3)]
    state = await wait_assistant_reply(
        _FakePage(early + done),
        before=_state(body_len=0, n=0),
        timeout_s=30,
        poll_ms=500,
        stable_polls=2,
    )
    assert "later answer" in state["body"]
    assert advance_clock["t"] < 1_030.0


@pytest.mark.asyncio
async def test_static_badge_raises_harvest_incomplete(advance_clock) -> None:
    """A quiet badge page raises and keeps the scrape (a:37226)."""
    badge = "Browsed files, edited a note."
    page = _FakePage([_state(body_len=len(badge), n=1, body=badge) for _ in range(20)])
    with pytest.raises(HarvestIncompleteError, match="timed out incomplete") as exc:
        await wait_assistant_reply(
            page,
            before=_state(body_len=0, n=0),
            timeout_s=2,
            poll_ms=500,
            stable_polls=2,
        )
    assert exc.value.body == badge
    assert advance_clock["t"] <= 1_002.5


@pytest.mark.asyncio
async def test_changing_badge_refreshes_idle_then_quiet_raises(advance_clock) -> None:
    """Same-length badge rewrites reset the idle budget. A quiet stretch expires."""
    same = "Browsed files, edited a note."
    other = "Updated files, edited a note."
    assert len(same) == len(other)
    changing = [
        _state(body_len=len(same), n=1, body=same if i % 2 == 0 else other)
        for i in range(10)
    ]
    quiet_body = changing[-1]["body"]
    quiet = [_state(body_len=len(quiet_body), n=1, body=quiet_body) for _ in range(12)]
    with pytest.raises(HarvestIncompleteError, match="timed out incomplete") as exc:
        await wait_assistant_reply(
            _FakePage(changing + quiet),
            before=_state(body_len=0, n=0),
            timeout_s=2,
            poll_ms=500,
            stable_polls=2,
        )
    assert exc.value.body == quiet_body
    assert advance_clock["t"] >= 1_004.0


@pytest.mark.asyncio
async def test_timestamp_tick_on_badge_does_not_refresh(advance_clock) -> None:
    """A trailing timestamp tick is not a scrape change. The idle budget expires."""
    badge = "Browsed files, edited a note."
    bodies = [
        f"{badge}\njust now" if i % 2 == 0 else f"{badge}\n1 minute ago"
        for i in range(20)
    ]
    page = _FakePage([_state(body_len=len(body), n=1, body=body) for body in bodies])
    with pytest.raises(HarvestIncompleteError, match="timed out incomplete"):
        await wait_assistant_reply(
            page,
            before=_state(body_len=0, n=0),
            timeout_s=2,
            poll_ms=500,
            stable_polls=2,
        )
    assert advance_clock["t"] <= 1_003.0


@pytest.mark.asyncio
async def test_wait_rejects_anchor_and_before_together() -> None:
    from claude_bundles.reply_anchor import ReplyAnchor

    page = _FakePage([])
    with pytest.raises(ValueError, match="not both"):
        await wait_assistant_reply(
            page,
            before={"n": 0, "body_len": 0},
            anchor=ReplyAnchor(marker="x"),
        )


def test_tail_hold_vetoes_tool_row_completion() -> None:
    body = "Mid reply prose.\nLoaded tools\nLoaded tools"
    state = _state(body_len=len(body), n=1, body=body)
    kwargs = {
        "base_len": 0,
        "base_n": 0,
        "min_growth": 1,
        "min_body": 1,
        "tail_hold": True,
    }
    assert _complete_enough(state, **kwargs) is False


@pytest.mark.asyncio
async def test_tail_hold_tool_row_then_prose_completes(advance_clock) -> None:
    """Doubled tool-row tail must not complete until prose lands (tail_hold)."""
    tool_tail = "Mid reply prose.\nLoaded tools\nLoaded tools"
    prose = tool_tail + "\n\nFinal answer after tools finished."
    page = _FakePage(
        [_state(body_len=len(tool_tail), n=1, body=tool_tail) for _ in range(3)]
        + [_state(body_len=len(prose), n=1, body=prose) for _ in range(3)]
    )
    state = await wait_assistant_reply(
        page,
        before=_state(body_len=0, n=0),
        tail_hold=True,
        timeout_s=30,
        poll_ms=500,
        stable_polls=2,
    )
    assert "Final answer" in state["body"]


@pytest.mark.asyncio
async def test_structural_quiet_tier_a_does_not_complete_tool_row_with_stop(
    advance_clock, structural_quiet_n
) -> None:
    """Tier A escape must not complete a landed tool-row tail while stop is up."""
    body = "Working prose.\nAgent Bus\nAgent Bus"
    quiet = {
        **_cowork_quiet_state(body_len=len(body), body=body),
        "stop": True,
    }
    page = _FakePage([quiet] * 12)
    with pytest.raises(HarvestIncompleteError, match="tail_hold unresolved"):
        await wait_assistant_reply(
            page,
            before=_state(body_len=0, n=0),
            tail_hold=True,
            timeout_s=1,
            poll_ms=500,
            min_growth=50,
            min_body=200,
            stable_polls=2,
        )


@pytest.mark.asyncio
async def test_anchor_miss_idle_classifies_observer_unverified(
    advance_clock,
) -> None:
    from cdp_ask.models import classify_stall_stage
    from cdp_ask.unverifiable import converse_stall_stage, is_unverifiable_stall

    from claude_bundles.reply_anchor import ReplyAnchor

    page = _FakePage(
        [
            {
                **_state(body_len=0, n=0),
                "anchored": True,
                "anchor_found": False,
                "anchor_matches": 0,
            }
        ]
        * 6
    )
    anchor = ReplyAnchor(marker="missing-marker-xyz", prior_matches=0)
    with pytest.raises(HarvestIncompleteError) as exc:
        await wait_assistant_reply(
            page,
            anchor=anchor,
            tail_hold=True,
            timeout_s=2,
            poll_ms=500,
            stable_polls=2,
        )
    msg = str(exc.value)
    assert exc.value.anchor_found is False
    assert "anchor not found at idle budget" in msg
    assert classify_stall_stage(msg) == "unknown"
    assert converse_stall_stage(msg, conv_ok=False) == "observer_unverified"
    assert is_unverifiable_stall(
        "observer_unverified", msg, url="https://claude.ai/cowork/cse_x"
    )


@pytest.mark.asyncio
async def test_tail_hold_changing_badge_key_runs_past_timeout(advance_clock) -> None:
    """Progress-key refresh extends idle budget past timeout_s for tail_hold."""
    tool_a = "Mid reply prose here.\nAgent Bus\nAgent Bus"
    tool_b = "Mid reply prose here.\nLoaded tools\nLoaded tools"
    changing = [
        _state(
            body_len=len(tool_a if i % 2 == 0 else tool_b),
            n=1,
            body=tool_a if i % 2 == 0 else tool_b,
        )
        for i in range(8)
    ]
    quiet = [_state(body_len=len(tool_a), n=1, body=tool_a) for _ in range(6)]
    page = _FakePage(changing + quiet)
    with pytest.raises(HarvestIncompleteError, match="tail_hold unresolved") as exc:
        await wait_assistant_reply(
            page,
            before=_state(body_len=0, n=0),
            tail_hold=True,
            timeout_s=2,
            poll_ms=500,
            stable_polls=2,
        )
    assert exc.value.tail_hold is True
    assert advance_clock["t"] >= 1_006.0


@pytest.mark.asyncio
async def test_before_without_tail_hold_completes_specimen_body(
    advance_clock,
) -> None:
    """Legacy before= path completes on specimen-shaped steer answer (no tail_hold)."""
    body = (
        "Steer answer prose here with Agent Bus label twice below.\n"
        "Agent Bus\nAgent Bus"
    )
    done = [_state(body_len=len(body), n=2, body=body) for _ in range(3)]
    state = await wait_assistant_reply(
        _FakePage(done),
        before=_state(body_len=0, n=0),
        timeout_s=30,
        poll_ms=500,
        stable_polls=2,
    )
    assert state["n"] == 2
    assert "Steer answer" in state["body"]


@pytest.mark.asyncio
async def test_tail_hold_idle_expiry_classifies_observer_unverified(
    advance_clock,
) -> None:
    from cdp_ask.models import classify_stall_stage
    from cdp_ask.unverifiable import converse_stall_stage, is_unverifiable_stall

    body = "Reply ends on tool row.\nAgent Bus\nAgent Bus"
    page = _FakePage([_state(body_len=len(body), n=1, body=body)] * 8)
    with pytest.raises(HarvestIncompleteError) as exc:
        await wait_assistant_reply(
            page,
            before=_state(body_len=0, n=0),
            tail_hold=True,
            timeout_s=2,
            poll_ms=500,
            stable_polls=2,
        )
    msg = str(exc.value)
    assert exc.value.tail_hold is True
    assert "tail_hold unresolved at idle budget" in msg
    assert classify_stall_stage(msg) == "unknown"
    assert (
        converse_stall_stage(msg, conv_ok=False) == "observer_unverified"
    )
    assert is_unverifiable_stall(
        "observer_unverified", msg, url="https://claude.ai/cowork/cse_x"
    )
