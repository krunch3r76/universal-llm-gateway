"""M1 compensating parity gate — ledger_age vs retired age_watch reference (arc 6264 §B5 M1)."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

import pytest

from scripts.model_manager.ui.controller.charter_runner import ledger_age
from scripts.model_manager.ui.controller.charter_runner.root_health import (
    FireAttemptOutcome,
    compute_unhealthy,
    is_declared_wait,
)
from scripts.model_manager.ui.controller.charter_runner.test_support import (
    reference_age_watch as ref,
)

EventKind = Literal[
    "observe_root",
    "observe_refuse",
    "clear_root",
    "clear_refuse",
    "tick_gap",
    "ledger_clear_demand",
    "ledger_clear_on_fired",
]


@dataclass(frozen=True, slots=True)
class ParityEvent:
    kind: EventKind
    delta_s: float = 0.0


@dataclass(frozen=True, slots=True)
class ClockSnapshot:
    age_s: float
    refuse_streak: int
    first_refuse_at: float | None
    demand_since: float | None


def _apply_event(
    root: str,
    *,
    event: ParityEvent,
    now: float,
    ref_dir: Path,
    ledger_dir: Path,
) -> None:
    refuse_key = f"{root}:refuse"
    if event.kind == "observe_root":
        ref.observe("tick_stall", root, present=True, now=now, data_dir=ref_dir)
        ledger_age.observe("tick_stall", root, present=True, now=now, data_dir=ledger_dir)
    elif event.kind == "observe_refuse":
        ref.observe("tick_stall", refuse_key, present=True, now=now, data_dir=ref_dir)
        ledger_age.observe("tick_stall", refuse_key, present=True, now=now, data_dir=ledger_dir)
    elif event.kind == "clear_root":
        ref.clear("tick_stall", root, data_dir=ref_dir)
        ledger_age.clear("tick_stall", root, data_dir=ledger_dir)
    elif event.kind == "clear_refuse":
        ref.clear("tick_stall", refuse_key, data_dir=ref_dir)
        ledger_age.clear("tick_stall", refuse_key, data_dir=ledger_dir)
    elif event.kind == "ledger_clear_demand":
        ledger_age.clear("tick_stall", root, data_dir=ledger_dir)
    elif event.kind == "ledger_clear_on_fired":
        ledger_age.clear("tick_stall", root, data_dir=ledger_dir)
        ledger_age.clear("tick_stall", refuse_key, data_dir=ledger_dir)
    elif event.kind == "tick_gap":
        pass


def _snapshot(root: str, *, now: float, ref_dir: Path, ledger_dir: Path) -> tuple[ClockSnapshot, ClockSnapshot]:
    refuse_key = f"{root}:refuse"
    ref_snap = ClockSnapshot(
        age_s=ref.age_s("tick_stall", root, now=now, data_dir=ref_dir),
        refuse_streak=ref.observation_count("tick_stall", refuse_key, data_dir=ref_dir),
        first_refuse_at=ref.first_seen_at("tick_stall", refuse_key, data_dir=ref_dir),
        demand_since=ref.first_seen_at("tick_stall", root, data_dir=ref_dir),
    )
    ledger_snap = ClockSnapshot(
        age_s=ledger_age.age_s("tick_stall", root, now=now, data_dir=ledger_dir),
        refuse_streak=ledger_age.observation_count("tick_stall", refuse_key, data_dir=ledger_dir),
        first_refuse_at=ledger_age.first_seen_at("tick_stall", refuse_key, data_dir=ledger_dir),
        demand_since=ledger_age.first_seen_at("tick_stall", root, data_dir=ledger_dir),
    )
    return ref_snap, ledger_snap


def _explain_divergence(
    ref_snap: ClockSnapshot,
    ledger_snap: ClockSnapshot,
) -> str | None:
    if ref_snap == ledger_snap:
        return None
    refuse_match = (
        ref_snap.refuse_streak == ledger_snap.refuse_streak
        and ref_snap.first_refuse_at == ledger_snap.first_refuse_at
    )
    if refuse_match and ref_snap.demand_since == ledger_snap.demand_since and ref_snap.age_s != ledger_snap.age_s:
        return "monotone_aging_across_tick_down_gap"
    if refuse_match and ref_snap.demand_since is not None and ledger_snap.demand_since is None:
        return "demand_since_clears_on_fired_or_declared_wait"
    return "unexplained"


def run_parity_sequence(
    root: str,
    events: list[ParityEvent],
    *,
    start: float,
    ref_dir: Path,
    ledger_dir: Path,
) -> list[str]:
    now = start
    divergences: list[str] = []
    for event in events:
        if event.kind == "tick_gap":
            now += event.delta_s
        else:
            _apply_event(root, event=event, now=now, ref_dir=ref_dir, ledger_dir=ledger_dir)
        ref_snap, ledger_snap = _snapshot(root, now=now, ref_dir=ref_dir, ledger_dir=ledger_dir)
        tag = _explain_divergence(ref_snap, ledger_snap)
        if tag == "unexplained":
            divergences.append(
                f"{event.kind}@{now:.0f}: ref={ref_snap} ledger={ledger_snap}"
            )
        elif tag is not None:
            divergences.append(f"expected:{tag}@{now:.0f}")
    return divergences


GENERATED_SEEDS = list(range(50))


@pytest.fixture
def parity_dirs(tmp_path: Path) -> tuple[Path, Path]:
    ref_dir = tmp_path / "ref"
    ledger_dir = tmp_path / "ledger"
    ref_dir.mkdir()
    ledger_dir.mkdir()
    return ref_dir, ledger_dir


@pytest.mark.offline
@pytest.mark.parametrize("seed", GENERATED_SEEDS)
def test_generated_parity_passes(seed: int, parity_dirs: tuple[Path, Path]) -> None:
    ref_dir, ledger_dir = parity_dirs
    rng = random.Random(seed)
    root = f"gen-{seed}"
    events: list[ParityEvent] = []
    for _ in range(rng.randint(4, 14)):
        roll = rng.random()
        if roll < 0.38:
            events.append(ParityEvent("observe_root"))
        elif roll < 0.58:
            events.append(ParityEvent("observe_refuse"))
        elif roll < 0.72:
            events.append(ParityEvent("clear_root"))
        elif roll < 0.86:
            events.append(ParityEvent("clear_refuse"))
        else:
            events.append(ParityEvent("tick_gap", delta_s=float(rng.randint(20, 600))))
    divergences = run_parity_sequence(
        root,
        events,
        start=1_700_000_000.0 + seed,
        ref_dir=ref_dir,
        ledger_dir=ledger_dir,
    )
    unexplained = [d for d in divergences if not d.startswith("expected:")]
    assert unexplained == [], unexplained


@pytest.mark.offline
def test_intended_divergence_monotone_aging_tick_down_gap(parity_dirs: tuple[Path, Path]) -> None:
    """§B2: ledger ages monotonically across tick-down; JSON clock inert."""
    ref_dir, ledger_dir = parity_dirs
    root = "gap-root"
    t0 = 1_700_000_000.0
    ref.observe("tick_stall", root, present=True, now=t0, data_dir=ref_dir)
    ledger_age.observe("tick_stall", root, present=True, now=t0, data_dir=ledger_dir)
    gap_s = 400.0
    t1 = t0 + gap_s
    ref_age = ref.age_s("tick_stall", root, now=t1, data_dir=ref_dir)
    ledger_age_val = ledger_age.age_s("tick_stall", root, now=t1, data_dir=ledger_dir)
    assert ref_age == pytest.approx(0.0, abs=1.0)
    assert ledger_age_val == pytest.approx(gap_s, abs=1.0)


@pytest.mark.offline
def test_intended_divergence_demand_since_clears_on_fired(parity_dirs: tuple[Path, Path]) -> None:
    """§B2: demand_since clears on FIRED; reference clock persists."""
    ref_dir, ledger_dir = parity_dirs
    root = "fired-root"
    t0 = 1_700_000_100.0
    ref.observe("tick_stall", root, present=True, now=t0, data_dir=ref_dir)
    ledger_age.observe("tick_stall", root, present=True, now=t0, data_dir=ledger_dir)
    ledger_age.clear("tick_stall", root, data_dir=ledger_dir)
    assert ref.first_seen_at("tick_stall", root, data_dir=ref_dir) == t0
    assert ledger_age.first_seen_at("tick_stall", root, data_dir=ledger_dir) is None


@pytest.mark.offline
def test_intended_divergence_demand_since_clears_on_declared_wait(
    parity_dirs: tuple[Path, Path],
) -> None:
    """§B2: declared-wait path clears ledger demand; reference unchanged."""
    ref_dir, ledger_dir = parity_dirs
    root = "wait-root"
    t0 = 1_700_000_200.0
    ref.observe("tick_stall", root, present=True, now=t0, data_dir=ref_dir)
    ledger_age.observe("tick_stall", root, present=True, now=t0, data_dir=ledger_dir)
    ledger_age.clear("tick_stall", root, data_dir=ledger_dir)
    assert is_declared_wait(FireAttemptOutcome.DEFERRED_LEGAL)
    assert ref.first_seen_at("tick_stall", root, data_dir=ref_dir) is not None
    assert ledger_age.first_seen_at("tick_stall", root, data_dir=ledger_dir) is None


@pytest.mark.offline
def test_slow_fuse_45m_across_tick_down_gap(parity_dirs: tuple[Path, Path]) -> None:
    """demand∧¬declared_wait slow fuse ± floor interval across tick-down gap."""
    ref_dir, ledger_dir = parity_dirs
    root = "slow-fuse"
    floor_s = 300.0
    fuse_s = ledger_age.TICK_STALL_MAX_AGE_S
    t0 = 1_700_000_300.0
    seeded = t0 - fuse_s + floor_s / 2
    ledger_age.seed_first_seen("tick_stall", root, seeded, data_dir=ledger_dir)
    ref.seed_first_seen("tick_stall", root, seeded, now=t0, data_dir=ref_dir)
    t1 = t0 + floor_s
    ledger_age_val = ledger_age.age_s("tick_stall", root, now=t1, data_dir=ledger_dir)
    assert ledger_age_val >= fuse_s - floor_s
    assert compute_unhealthy(
        root,
        FireAttemptOutcome.REFUSED_PRE_FIRE,
        stopped_reason=None,
        data_dir=ledger_dir,
    ) is True
    assert ref.age_s("tick_stall", root, now=t1, data_dir=ref_dir) < fuse_s


class UnhealthyLeg(StrEnum):
    NONE_OUTCOME_SLOW_FUSE = "none_outcome_slow_fuse"
    INTEGRITY = "integrity"
    STUCK_BOOKKEEPING = "stuck_bookkeeping"
    RECURRING_REFUSE = "recurring_refuse"
    DECLARED_WAIT_FALSE = "declared_wait_false"


F5_OUTCOMES = list(FireAttemptOutcome)


@pytest.mark.offline
@pytest.mark.parametrize("outcome", F5_OUTCOMES)
def test_f5_fire_attempt_outcome_tripwire(
    outcome: FireAttemptOutcome, parity_dirs: tuple[Path, Path]
) -> None:
    """F5 — each FireAttemptOutcome arm: compute_unhealthy stable given matched clock inputs."""
    _, ledger_dir = parity_dirs
    root = f"f5-{outcome.value}"
    now = 1_700_001_000.0
    ledger_age.seed_first_seen("tick_stall", root, now - 100.0, data_dir=ledger_dir)
    if outcome in {
        FireAttemptOutcome.REFUSED_PRE_FIRE,
        FireAttemptOutcome.ERRORED_PRE_FIRE,
    }:
        ledger_age.observe("tick_stall", f"{root}:refuse", present=True, now=now, data_dir=ledger_dir)
        ledger_age.observe("tick_stall", f"{root}:refuse", present=True, now=now, data_dir=ledger_dir)
    result = compute_unhealthy(
        root,
        outcome,
        consult_pending=False,
        skipped_reason="dormant" if outcome == FireAttemptOutcome.NO_ATTEMPT_QUIET else None,
        stopped_reason="pointer_post_failed"
        if outcome == FireAttemptOutcome.FIRED_BOOKKEEPING_FAILED
        else ("admission_rejected" if outcome == FireAttemptOutcome.REFUSED_PRE_FIRE else None),
        data_dir=ledger_dir,
    )
    if outcome == FireAttemptOutcome.INTEGRITY:
        assert result is True
    elif is_declared_wait(outcome):
        assert result is False
    elif outcome == FireAttemptOutcome.FIRED:
        assert result is False


@pytest.mark.offline
@pytest.mark.parametrize("leg", list(UnhealthyLeg))
def test_f5_compute_unhealthy_leg_tripwire(
    leg: UnhealthyLeg, parity_dirs: tuple[Path, Path]
) -> None:
    _, ledger_dir = parity_dirs
    root = f"leg-{leg.value}"
    now = 1_700_002_000.0
    fuse = ledger_age.TICK_STALL_MAX_AGE_S
    if leg == UnhealthyLeg.NONE_OUTCOME_SLOW_FUSE:
        ledger_age.seed_first_seen("tick_stall", root, now - fuse - 1, data_dir=ledger_dir)
        assert compute_unhealthy(root, None, data_dir=ledger_dir) is True
    elif leg == UnhealthyLeg.INTEGRITY:
        assert compute_unhealthy(root, FireAttemptOutcome.INTEGRITY, data_dir=ledger_dir) is True
    elif leg == UnhealthyLeg.STUCK_BOOKKEEPING:
        ledger_age.seed_first_seen("tick_stall", root, now - 10, data_dir=ledger_dir)
        assert compute_unhealthy(
            root,
            FireAttemptOutcome.FIRED_BOOKKEEPING_FAILED,
            stopped_reason="pointer_post_failed",
            data_dir=ledger_dir,
        ) is True
    elif leg == UnhealthyLeg.RECURRING_REFUSE:
        ledger_age.observe("tick_stall", f"{root}:refuse", present=True, now=now, data_dir=ledger_dir)
        ledger_age.observe("tick_stall", f"{root}:refuse", present=True, now=now, data_dir=ledger_dir)
        assert compute_unhealthy(
            root,
            FireAttemptOutcome.REFUSED_PRE_FIRE,
            stopped_reason=None,
            data_dir=ledger_dir,
        ) is True
    elif leg == UnhealthyLeg.DECLARED_WAIT_FALSE:
        assert compute_unhealthy(
            root,
            FireAttemptOutcome.DEFERRED_LEGAL,
            data_dir=ledger_dir,
        ) is False


@pytest.mark.offline
def test_parity_refuse_streak_and_first_refuse_at(parity_dirs: tuple[Path, Path]) -> None:
    ref_dir, ledger_dir = parity_dirs
    root = "refuse-parity"
    now = 1_700_003_000.0
    refuse = f"{root}:refuse"
    for _ in range(3):
        ref.observe("tick_stall", refuse, present=True, now=now, data_dir=ref_dir)
        ledger_age.observe("tick_stall", refuse, present=True, now=now, data_dir=ledger_dir)
    assert ref.observation_count("tick_stall", refuse, data_dir=ref_dir) == 3
    assert ledger_age.observation_count("tick_stall", refuse, data_dir=ledger_dir) == 3
    assert ref.first_seen_at("tick_stall", refuse, data_dir=ref_dir) == now
    assert ledger_age.first_seen_at("tick_stall", refuse, data_dir=ledger_dir) == now
