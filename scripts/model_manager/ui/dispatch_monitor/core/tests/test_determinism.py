"""Falsifier F2 -- the cheapest high-value test in the plan.

Replay a fixture twice at the same ``now_ms``; the fingerprints must match. A
mismatch means the Model is not pure: a hidden clock, dict ordering, or set
iteration leaked into derivation. Fable named this the determinism falsifier and
borrowed it from ESAA's replay-and-compare-hash audit primitive.

Also pins the two properties the fingerprint's *other* job depends on -- excluding
age so a quiescent system publishes nothing, while still including attention
membership so a threshold crossing does publish.
"""

from __future__ import annotations

from .conftest import replay

from scripts.model_manager.ui.dispatch_monitor.core import fingerprint as fingerprint_mod


def test_replay_twice_yields_identical_fingerprint(any_fixture: str) -> None:
    """F2: two independent replays of one fixture agree byte-for-byte."""
    first_model, first_now = replay(any_fixture)
    second_model, second_now = replay(any_fixture)
    assert first_now == second_now
    first = first_model.derive(first_now)
    second = second_model.derive(second_now)
    assert first.fingerprint == second.fingerprint
    assert fingerprint_mod.fingerprint_payload(
        first
    ) == fingerprint_mod.fingerprint_payload(second)
    assert first == second


def test_repeated_derive_on_one_model_is_stable(any_fixture: str) -> None:
    """Deriving twice from one model without folding must not change the frame."""
    model, now = replay(any_fixture)
    assert model.derive(now).fingerprint == model.derive(now).fingerprint


def test_fingerprint_ignores_clock_advance_when_nothing_changed() -> None:
    """A quiescent system must publish nothing, however far the clock advances.

    This is why age fields are excluded from the hash: if they were included, a
    30 Hz tick over an idle system would emit a new frame every tick and the
    fingerprint would suppress nothing.
    """
    model, now = replay("charter-admit-run-terminal.jsonl")
    baseline = model.derive(now)
    later = model.derive(now + 1_000)
    assert later.generated_at_ms != baseline.generated_at_ms
    assert later.fingerprint == baseline.fingerprint


def test_fingerprint_changes_when_attention_crosses_a_threshold() -> None:
    """Age exclusion must not mute real state change.

    The clock advance here pushes a live CDP leg past ⅔ max_wall_s. The
    resulting wall-approaching attention item is new *membership*, which is hashed.
    """
    model, now = replay("cdp-leg.jsonl")
    quiet = model.derive(now)
    stale = model.derive(now + 1_210_000)
    assert stale.fingerprint != quiet.fingerprint
    assert any(item.kind == "cdp.leg.wall_approaching" for item in stale.attention)


def test_fold_order_independence_across_sources() -> None:
    """Two models fed the same records via different call paths must agree."""
    from .conftest import load

    from scripts.model_manager.ui.dispatch_monitor.core.model import Model

    source = load("gs2-dual-emitter.jsonl")
    subscribed = Model()
    source.subscribe(subscribed.apply)
    bulk = Model()
    bulk.apply_all(source.records)
    now = source.max_ts()
    assert subscribed.derive(now).fingerprint == bulk.derive(now).fingerprint
