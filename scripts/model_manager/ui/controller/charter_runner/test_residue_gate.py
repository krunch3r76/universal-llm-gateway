"""Residue-admit gate — fingerprint, witnesses, store, acceptance spine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    append_footer_to_packet,
    footer_kwargs_for_window,
)
from scripts.model_manager.ui.controller.charter_runner.admission import (
    Decision,
    evaluate_root,
)
from scripts.model_manager.ui.controller.charter_runner.residue_fingerprint import (
    REASON_NO_PROGRESS,
    REASON_UNCHANGED_RESIDUE,
    UNCHANGED_RESIDUE_SKIP_THRESHOLD,
    ResidueRecord,
    build_witness_tuple,
    compute_fingerprint,
    evaluate_residue_gate,
    load_residue_record,
    normalize_next_pickup,
    record_from_harvest,
    save_residue_record,
    witness_fired,
)


def build_consult_stall_requeue_checkpoint(*_a, **_k):  # noqa: ANN001
    """Stub — Phase 3 deleted consult_stall_build; heal-path tests are skipped."""
    raise RuntimeError("consult_stall_build deleted at Phase 3")


def build_self_heal_checkpoint(*_a, **_k):  # noqa: ANN001
    """Stub — Phase 3 deleted self_heal_checkpoint; heal-path tests are skipped."""
    raise RuntimeError("self_heal_checkpoint deleted at Phase 3")

_RESUME = (
    "— RESUME (any seat, no command): load agent-bus-discipline "
    "(§ Standing root threads + § R12 completeness gate) → read scoreboard "
    "→ this is the latest CHECKPOINT. empty Next-pickup ≠ arc complete."
)

_BASE_CHECKPOINT = (
    """\
# CHECKPOINT — wave 3

## Anchor
- Author: worker
- Scoreboard: cortex://notes/system/threads/5870-charter-scoreboard.md

## Steps
1. [x] G1 — question
2. [x] G2 — answer
3. [ ] G3 — R-admit

## In-flight / WIP
none

## Next pickup
1. CONSULT_PENDING — G3 R-admit · consult_role: r_admit · executor_lane: judgment

## Frictions
_None this window._

## Sidecars
- Dense spec: cortex://notes/system/specs/foo-dense.md · spec_sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

## What happened (plain)
Worker posted CHECKPOINT.

Scoreboard: cortex://notes/system/threads/5870-charter-scoreboard.md

"""
    + _RESUME
)


def _turn(n: int, subject: str, body: str) -> dict:
    return {"turn_number": n, "subject": subject, "body": body}


def _fp(
    *,
    body: str | None = None,
    admission_mode: str = "consult",
    window_kind: str = "consult",
) -> str:
    cp = body or _BASE_CHECKPOINT
    parsed = parse_checkpoint(cp)
    witness = build_witness_tuple(checkpoint_body=cp, parsed=parsed)
    return compute_fingerprint(
        parsed=parsed,
        admission_mode=admission_mode,
        window_kind=window_kind,
        witness=witness,
    )


def _record(
    *, body: str | None = None, skip_count: int = 0, store_dir: Path
) -> ResidueRecord:
    cp = body or _BASE_CHECKPOINT
    witness = build_witness_tuple(checkpoint_body=cp, parsed=parse_checkpoint(cp))
    record = ResidueRecord(
        fingerprint=_fp(body=body),
        witness=witness,
        consecutive_skip_count=skip_count,
    )
    save_residue_record("5870", record, store_dir=store_dir)
    return record


@pytest.fixture
def residue_dir(tmp_path: Path) -> Path:
    return tmp_path / "last-residue"


@pytest.fixture
def patch_residue_dir(residue_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.residue_store._default_store_dir",
        lambda: residue_dir,
    )
    return residue_dir


@pytest.fixture
def patch_admission_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel.host._admission_mode_from_env",
        lambda env, root_id: "autonomous",
    )


_EXCLUDE_CASES = {
    "checkpoint_turn": lambda b: b,
    "window_index": lambda b: b.replace("wave 3", "wave 17"),
    "poll_hint": lambda b: b.replace(
        "consult_role: r_admit",
        "consult_role: r_admit · poll_hint=turn:99",
    ),
    "from_cdp": lambda b: b.replace(
        "consult_role: r_admit",
        "consult_role: r_admit · from=web-anthropic:turn:88",
    ),
    "execution_id": lambda b: b.replace(
        "consult_role: r_admit",
        "consult_role: r_admit · execution_id=abc123def",
    ),
    "generation_marker": lambda b: b.replace(
        "## Anchor",
        "## Anchor\n- Generation: heal:consult_stall gen=4",
    ),
    "sidecars_lineage": lambda b: b.replace(
        "## Sidecars",
        "## Sidecars\n- agent-bus:5884 — prior window transcript",
    ),
    "frictions_assertion": lambda b: b.replace(
        "_None this window._",
        "- filed-assertion: friction:26100",
    ),
    "what_happened": lambda b: b.replace(
        "Worker posted CHECKPOINT.",
        "Totally different prose about nothing.",
    ),
    "consult_provenance": lambda b: b.replace(
        "## What happened",
        "## Consult provenance\n- consult_thread: agent-bus:9000\n- verdict: ADMIT\n"
        "- consultant_family: anthropic\n- consultant_substrate: web-anthropic\n\n## What happened",
    ),
    "subject_window_suffix": lambda b: b,
    "materialized_window_line": lambda b: b.replace(
        "G3 R-admit",
        "G3 R-admit (charter-runner window 10)",
    ),
}


@pytest.mark.parametrize("field_name", list(_EXCLUDE_CASES))
@pytest.mark.offline
def test_ac1_exclude_field_does_not_change_fingerprint(
    field_name: str, patch_admission_mode: None
) -> None:
    variant = _EXCLUDE_CASES[field_name](_BASE_CHECKPOINT)
    assert _fp(body=_BASE_CHECKPOINT) == _fp(body=variant), field_name


@pytest.mark.offline
def test_w1_pickup_text_changed(patch_admission_mode: None) -> None:
    last = build_witness_tuple(
        checkpoint_body=_BASE_CHECKPOINT, parsed=parse_checkpoint(_BASE_CHECKPOINT)
    )
    advanced = _BASE_CHECKPOINT.replace(
        "1. CONSULT_PENDING — G3 R-admit",
        "1. CONSULT_PENDING — G3 R-admit REVISED",
    )
    current = build_witness_tuple(
        checkpoint_body=advanced, parsed=parse_checkpoint(advanced)
    )
    fired, wid = witness_fired(current, last)
    assert fired and wid == "W1"


@pytest.mark.offline
def test_w2_steps_done_increased(patch_admission_mode: None) -> None:
    last = build_witness_tuple(
        checkpoint_body=_BASE_CHECKPOINT, parsed=parse_checkpoint(_BASE_CHECKPOINT)
    )
    done = _BASE_CHECKPOINT.replace("3. [ ] G3", "3. [x] G3")
    current = build_witness_tuple(checkpoint_body=done, parsed=parse_checkpoint(done))
    fired, wid = witness_fired(current, last)
    assert fired and wid == "W2"


@pytest.mark.offline
def test_w3_poll_hint_changed(patch_admission_mode: None) -> None:
    base = _BASE_CHECKPOINT.replace(
        "consult_role: r_admit",
        "consult_role: r_admit · poll_hint=turn:1",
    )
    advanced = base.replace("poll_hint=turn:1", "poll_hint=turn:2")
    last = build_witness_tuple(checkpoint_body=base, parsed=parse_checkpoint(base))
    current = build_witness_tuple(
        checkpoint_body=advanced, parsed=parse_checkpoint(advanced)
    )
    fired, wid = witness_fired(current, last)
    assert fired and wid == "W3"


@pytest.mark.offline
def test_w4_execution_id_changed(patch_admission_mode: None) -> None:
    base = _BASE_CHECKPOINT.replace(
        "consult_role: r_admit",
        "consult_role: r_admit · execution_id=aaa",
    )
    advanced = base.replace("execution_id=aaa", "execution_id=bbb")
    last = build_witness_tuple(checkpoint_body=base, parsed=parse_checkpoint(base))
    current = build_witness_tuple(
        checkpoint_body=advanced, parsed=parse_checkpoint(advanced)
    )
    fired, wid = witness_fired(current, last)
    assert fired and wid == "W4"


@pytest.mark.offline
@pytest.mark.skip(reason="Phase 3: heal/stall builders deleted")
def test_w5_generation_marker_changed(patch_admission_mode: None) -> None:
    prior = parse_checkpoint(_BASE_CHECKPOINT)
    _, body_g1 = build_consult_stall_requeue_checkpoint(
        prior=prior, window_index=9, worker_thread="5750", child_refs=[], generation=1
    )
    _, body_g2 = build_consult_stall_requeue_checkpoint(
        prior=prior, window_index=9, worker_thread="5750", child_refs=[], generation=2
    )
    last = build_witness_tuple(
        checkpoint_body=body_g1, parsed=parse_checkpoint(body_g1)
    )
    current = build_witness_tuple(
        checkpoint_body=body_g2, parsed=parse_checkpoint(body_g2)
    )
    fired, wid = witness_fired(current, last)
    assert fired and wid == "W5"


@pytest.mark.offline
def test_w6_consult_provenance_newly_populated(patch_admission_mode: None) -> None:
    last = build_witness_tuple(
        checkpoint_body=_BASE_CHECKPOINT, parsed=parse_checkpoint(_BASE_CHECKPOINT)
    )
    with_prov = _EXCLUDE_CASES["consult_provenance"](_BASE_CHECKPOINT)
    current = build_witness_tuple(
        checkpoint_body=with_prov, parsed=parse_checkpoint(with_prov)
    )
    fired, wid = witness_fired(current, last)
    assert fired and wid == "W6"


@pytest.mark.offline
def test_w7_spec_sha256_changed(patch_admission_mode: None) -> None:
    last = build_witness_tuple(
        checkpoint_body=_BASE_CHECKPOINT, parsed=parse_checkpoint(_BASE_CHECKPOINT)
    )
    rotated = _BASE_CHECKPOINT.replace("a" * 64, "b" * 64)
    current = build_witness_tuple(
        checkpoint_body=rotated, parsed=parse_checkpoint(rotated)
    )
    fired, wid = witness_fired(current, last)
    assert fired and wid == "W7"


@pytest.mark.offline
def test_w8_steps_signature_without_pickup_change(patch_admission_mode: None) -> None:
    last = build_witness_tuple(
        checkpoint_body=_BASE_CHECKPOINT, parsed=parse_checkpoint(_BASE_CHECKPOINT)
    )
    status_only = _BASE_CHECKPOINT.replace("3. [ ] G3", "3. [~] G3")
    current = build_witness_tuple(
        checkpoint_body=status_only, parsed=parse_checkpoint(status_only)
    )
    fired, wid = witness_fired(current, last)
    assert fired and wid == "W8"


@pytest.mark.offline
def test_w9_scoreboard_lane_hash_hook_only() -> None:
    import scripts.model_manager.ui.controller.charter_runner.residue_fingerprint as mod

    assert "W9 scoreboard_lane_hash" in Path(mod.__file__).read_text(encoding="utf-8")


@pytest.mark.offline
@pytest.mark.skip(reason="Phase 3: heal/stall builders deleted")
def test_w10_self_heal_one_shot(
    patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    prior = parse_checkpoint(_BASE_CHECKPOINT)
    _, heal_body = build_self_heal_checkpoint(
        prior=prior,
        window_index=3,
        worker_thread="5884",
        reason="checkpoint_missing",
        root_id="5870",
    )
    _record(body=heal_body, store_dir=patch_residue_dir)
    gate = evaluate_residue_gate(
        checkpoint_body=heal_body,
        parsed=parse_checkpoint(heal_body),
        admission_mode="consult",
        window_kind="consult",
        last=load_residue_record("5870", store_dir=patch_residue_dir),
        window_index=1,
    )
    assert gate.admit is True
    assert gate.w10_consumed is True
    gate2 = evaluate_residue_gate(
        checkpoint_body=heal_body,
        parsed=parse_checkpoint(heal_body),
        admission_mode="consult",
        window_kind="consult",
        last=ResidueRecord(
            fingerprint=gate.fingerprint,
            witness=gate.witness,
            consecutive_skip_count=0,
            w10_consumed=True,
            last_window_index=1,
        ),
        window_index=2,
    )
    assert gate2.admit is False
    assert gate2.reason == REASON_UNCHANGED_RESIDUE


@pytest.mark.offline
def test_ac3_no_checkpoint_turn_or_branch() -> None:
    import scripts.model_manager.ui.controller.charter_runner.admission as elig

    source = Path(elig.__file__).read_text(encoding="utf-8")
    assert "checkpoint_turn > last" not in source


@pytest.mark.offline
def test_ac4_missing_store_admits(
    patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    turns = [_turn(10, "CHECKPOINT wave 3", _BASE_CHECKPOINT)]
    decision = evaluate_root("5870", turns, CapStore())
    assert decision.eligible is True


@pytest.mark.offline
def test_ac4_corrupt_store_admits(
    patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    patch_residue_dir.mkdir(parents=True, exist_ok=True)
    (patch_residue_dir / "5870.json").write_text("{not-json", encoding="utf-8")
    turns = [_turn(10, "CHECKPOINT wave 3", _BASE_CHECKPOINT)]
    decision = evaluate_root("5870", turns, CapStore())
    assert decision.eligible is True


@pytest.mark.offline
def test_ac3_k_consecutive_skips_stop(
    patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    _record(
        store_dir=patch_residue_dir,
        skip_count=UNCHANGED_RESIDUE_SKIP_THRESHOLD - 1,
    )
    caps = CapStore()
    turns = [_turn(10, "CHECKPOINT wave 3", _BASE_CHECKPOINT)]
    decision = evaluate_root("5870", turns, caps)
    assert decision.eligible is False
    assert decision.reason == REASON_NO_PROGRESS
    allowed, stop = caps.check("5870")
    assert not allowed
    assert stop == f"stopped:{REASON_NO_PROGRESS}"


@pytest.mark.offline
def test_same_window_index_does_not_increment_residue_skip(
    patch_admission_mode: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured root-5918 fingerprint: same window must not burn strike budget."""
    measured_fp = "a6fe9c0e657a8b0900b1a8bd985485acd70376f9ccb91400c20d2c19c3a6f7c7"
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.residue_fingerprint."
        "compute_fingerprint",
        lambda **_kwargs: measured_fp,
    )
    parsed = parse_checkpoint(_BASE_CHECKPOINT)
    witness = build_witness_tuple(checkpoint_body=_BASE_CHECKPOINT, parsed=parsed)
    initial_skip = 1
    last = ResidueRecord(
        fingerprint=measured_fp,
        witness=witness,
        consecutive_skip_count=initial_skip,
        last_window_index=7,
    )
    for _ in range(3):
        gate = evaluate_residue_gate(
            checkpoint_body=_BASE_CHECKPOINT,
            parsed=parsed,
            admission_mode="consult",
            window_kind="consult",
            last=last,
            window_index=7,
        )
        assert gate.stop_root is False
        assert gate.consecutive_skip_count == initial_skip
        assert gate.reason == REASON_UNCHANGED_RESIDUE


@pytest.mark.offline
def test_spine_skip_consult_duplicates(
    patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    """5870 consult template w3≡w10≡w17 — identical CHECKPOINT bodies, first re-admit skips."""
    _record(store_dir=patch_residue_dir)
    turns = [_turn(99, "CHECKPOINT — window 10", _BASE_CHECKPOINT)]
    decision = evaluate_root("5870", turns, CapStore())
    assert decision.eligible is False
    assert decision.reason == REASON_UNCHANGED_RESIDUE


@pytest.mark.offline
def test_spine_must_admit_first_window(
    patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    turns = [_turn(1, "CHECKPOINT start", _BASE_CHECKPOINT)]
    assert evaluate_root("5870", turns, CapStore()).eligible is True


@pytest.mark.offline
def test_spine_must_admit_pickup_advance(
    patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    _record(store_dir=patch_residue_dir)
    advanced = _BASE_CHECKPOINT.replace("G3 — R-admit", "G4 — implement bind")
    turns = [_turn(20, "CHECKPOINT advanced", advanced)]
    assert evaluate_root("5870", turns, CapStore()).eligible is True


@pytest.mark.offline
def test_spine_must_admit_step_done(
    patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    _record(store_dir=patch_residue_dir)
    done = _BASE_CHECKPOINT.replace("3. [ ] G3", "3. [x] G3")
    turns = [_turn(20, "CHECKPOINT step done", done)]
    assert evaluate_root("5870", turns, CapStore()).eligible is True


@pytest.mark.offline
def test_spine_must_admit_of2_poll_hint_change(
    patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    base = _BASE_CHECKPOINT.replace(
        "consult_role: r_admit",
        "consult_role: r_admit · poll_hint=turn:1",
    )
    _record(body=base, store_dir=patch_residue_dir)
    advanced = base.replace("poll_hint=turn:1", "poll_hint=turn:2")
    turns = [_turn(20, "CHECKPOINT OF2", advanced)]
    assert evaluate_root("5870", turns, CapStore()).eligible is True


@pytest.mark.offline
@pytest.mark.skip(reason="Phase 3: heal/stall builders deleted")
def test_spine_consult_stall_admit_then_skip(
    patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    prior = parse_checkpoint(_BASE_CHECKPOINT)
    _, body_g1 = build_consult_stall_requeue_checkpoint(
        prior=prior, window_index=9, worker_thread="5750", child_refs=[], generation=1
    )
    _record(body=_BASE_CHECKPOINT, store_dir=patch_residue_dir)
    turns = [_turn(30, "CHECKPOINT gen1", body_g1)]
    assert evaluate_root("5870", turns, CapStore()).eligible is True
    save_residue_record(
        "5870",
        record_from_harvest(
            checkpoint_body=body_g1,
            parsed=parse_checkpoint(body_g1),
            admission_mode="consult",
            window_kind="consult",
        ),
        store_dir=patch_residue_dir,
    )
    turns = [_turn(31, "CHECKPOINT gen1 again", body_g1)]
    decision = evaluate_root("5870", turns, CapStore())
    assert decision.eligible is False
    assert decision.reason == REASON_UNCHANGED_RESIDUE


@pytest.mark.offline
@pytest.mark.skip(reason="Phase 3: heal/stall builders deleted")
def test_spine_self_heal_admit_then_skip(
    patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    prior = parse_checkpoint(_BASE_CHECKPOINT)
    _, heal_body = build_self_heal_checkpoint(
        prior=prior,
        window_index=3,
        worker_thread="5884",
        reason="checkpoint_missing",
        root_id="5870",
    )
    _record(store_dir=patch_residue_dir)
    turns = [_turn(40, "CHECKPOINT self-heal", heal_body)]
    assert evaluate_root("5870", turns, CapStore()).eligible is True
    save_residue_record(
        "5870",
        record_from_harvest(
            checkpoint_body=heal_body,
            parsed=parse_checkpoint(heal_body),
            admission_mode="consult",
            window_kind="consult",
            w10_consumed=True,
        ),
        store_dir=patch_residue_dir,
    )
    turns = [_turn(41, "CHECKPOINT self-heal repeat", heal_body)]
    decision = evaluate_root("5870", turns, CapStore())
    assert decision.eligible is False


@pytest.mark.offline
def test_spine_spec_sha_refresh_admits(
    patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    _record(store_dir=patch_residue_dir)
    refreshed = _BASE_CHECKPOINT.replace("a" * 64, "c" * 64)
    turns = [_turn(50, "CHECKPOINT sha refresh", refreshed)]
    assert evaluate_root("5870", turns, CapStore()).eligible is True


@pytest.mark.offline
def test_spine_consult_pending_cleared_admits(
    patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    _record(store_dir=patch_residue_dir)
    cleared = _EXCLUDE_CASES["consult_provenance"](_BASE_CHECKPOINT).replace(
        "CONSULT_PENDING — G3 R-admit",
        "G4 — implement after R-admit",
    )
    turns = [_turn(60, "CHECKPOINT cleared consult", cleared)]
    assert evaluate_root("5870", turns, CapStore()).eligible is True


@pytest.mark.offline
def test_normalize_next_pickup_strips_transport(patch_admission_mode: None) -> None:
    body = _BASE_CHECKPOINT.replace(
        "consult_role: r_admit",
        "consult_role: r_admit · poll_hint=turn:5 · execution_id=xyz",
    )
    parsed = parse_checkpoint(body)
    joined = " | ".join(normalize_next_pickup(parsed))
    assert "poll_hint" not in joined
    assert "execution_id" not in joined


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac6_skip_emits_fingerprint(
    patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    from scripts.model_manager.ui.controller.charter_runner.state_close import (
        emit_skip_and_maybe_state_close,
    )

    _record(store_dir=patch_residue_dir)
    fp = _fp()
    decision = Decision(
        False,
        REASON_UNCHANGED_RESIDUE,
        "5870",
        checkpoint=_turn(10, "CHECKPOINT", _BASE_CHECKPOINT),
        parsed=parse_checkpoint(_BASE_CHECKPOINT),
        residue_fingerprint=fp,
        half="body",
    )
    skipped: dict[str, int] = {}
    with patch(
        "scripts.model_manager.ui.controller.charter_runner.state_close.events.emit_manage_charter_tick_root_skipped",
        new_callable=AsyncMock,
    ) as mock_emit:
        await emit_skip_and_maybe_state_close(
            decision,
            state_closes_this_tick=0,
            skipped_by_reason=skipped,
        )
    kwargs = mock_emit.await_args.kwargs
    assert kwargs["root"] == "5870"
    assert kwargs["reason"] == REASON_UNCHANGED_RESIDUE
    assert kwargs["fingerprint"] == fp


@pytest.mark.offline
def test_harvest_persists_store(
    patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import harvest

    harvest._persist_residue_after_harvest(
        root_id="5870",
        consumed_checkpoint_body=_BASE_CHECKPOINT,
        admission_meta={"admission_mode": "consult"},
    )
    loaded = load_residue_record("5870", store_dir=patch_residue_dir)
    assert loaded is not None
    assert loaded.consecutive_skip_count == 0
    assert loaded.fingerprint == _fp(admission_mode="consult", window_kind="consult")


_ADMISSION = _turn(
    11,
    "WIP charter-runner window 4 — agent-bus:5999",
    '{"window": 4, "worker_thread": "5999", "admission_mode": "consult"}',
)


def _checkpoint_with_footer(
    body: str, *, root_id: str = "5870", window_index: int = 4
) -> str:
    """Post-DIRECTIVE-10 harvest requires a valid ```charter-state``` footer."""
    return append_footer_to_packet(
        body, **footer_kwargs_for_window(root_id, window_index)
    )


@pytest.fixture
def harvest_env(monkeypatch: pytest.MonkeyPatch):
    """Isolate harvest's collaborators so only the residue write is under test."""
    from scripts.model_manager.ui.controller.charter_runner import harvest

    monkeypatch.setattr(harvest.bus_client, "fetch_turns", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        harvest.bus_client,
        "fetch_thread",
        AsyncMock(return_value={"slug": "arc", "summary": "so what"}),
    )
    monkeypatch.setattr(harvest.bus_client, "close_worker_thread", AsyncMock())
    monkeypatch.setattr(harvest, "after_window_terminal_harvested", AsyncMock())
    monkeypatch.setattr(harvest.window_log, "already_harvested", lambda *a, **k: False)
    monkeypatch.setattr(harvest.window_log, "append_closeout", lambda *a, **k: None)
    monkeypatch.setattr(harvest.events, "emit_manage_charter_tick_closed", AsyncMock())
    return harvest


@pytest.mark.offline
@pytest.mark.asyncio
async def test_harvest_records_consumed_not_post_window_checkpoint(
    harvest_env, patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    """The window's own CHECKPOINT must never become the residue it is compared to."""
    produced = _checkpoint_with_footer(
        _BASE_CHECKPOINT.replace("3. [ ] G3", "3. [x] G3")
    )
    turns = [
        _turn(10, "CHECKPOINT wave 3", _BASE_CHECKPOINT),
        _ADMISSION,
        _turn(12, "CHECKPOINT wave 4", produced),
    ]
    await harvest_env.harvest_completed_windows("5870", turns)
    loaded = load_residue_record("5870", store_dir=patch_residue_dir)
    assert loaded is not None
    assert loaded.fingerprint == _fp(body=_BASE_CHECKPOINT)
    assert loaded.fingerprint != _fp(body=produced)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_harvested_window_leaves_root_eligible(
    harvest_env, patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    """Anti-deadlock spine: a productive window must not starve the next tick."""
    produced = _checkpoint_with_footer(
        _BASE_CHECKPOINT.replace("3. [ ] G3", "3. [x] G3")
    )
    turns = [
        _turn(10, "CHECKPOINT wave 3", _BASE_CHECKPOINT),
        _ADMISSION,
        _turn(12, "CHECKPOINT wave 4", produced),
    ]
    await harvest_env.harvest_completed_windows("5870", turns)
    assert evaluate_root("5870", turns, CapStore()).eligible is True


@pytest.mark.offline
@pytest.mark.asyncio
async def test_thrash_across_window_boundary_skips_then_stops(
    harvest_env, patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    """A window that reproduces its input residue still trips the thrash gate.

    Strikes advance only when ``window_index`` advances — re-evaluating the same
    next-window does not escalate to ``no_progress``.
    """
    produced = _checkpoint_with_footer(
        _EXCLUDE_CASES["what_happened"](_BASE_CHECKPOINT)
    )
    turns = [
        _turn(10, "CHECKPOINT wave 3", _BASE_CHECKPOINT),
        _ADMISSION,
        _turn(12, "CHECKPOINT wave 4", produced),
    ]
    await harvest_env.harvest_completed_windows("5870", turns)
    caps = CapStore()
    first = evaluate_root("5870", turns, caps)
    assert first.eligible is False
    assert first.reason == REASON_UNCHANGED_RESIDUE
    # Same next-window re-tick must not escalate the strike count.
    second = evaluate_root("5870", turns, caps)
    assert second.eligible is False
    assert second.reason == REASON_UNCHANGED_RESIDUE
    # Force strike count to threshold-1 with a prior window index, then advance.
    loaded = load_residue_record("5870", store_dir=patch_residue_dir)
    assert loaded is not None
    save_residue_record(
        "5870",
        ResidueRecord(
            fingerprint=loaded.fingerprint,
            witness=loaded.witness,
            consecutive_skip_count=UNCHANGED_RESIDUE_SKIP_THRESHOLD - 1,
            w10_consumed=loaded.w10_consumed,
            last_window_index=loaded.last_window_index - 1,
        ),
        store_dir=patch_residue_dir,
    )
    third = evaluate_root("5870", turns, caps)
    assert third.reason == REASON_NO_PROGRESS
    assert caps.check("5870") == (False, f"stopped:{REASON_NO_PROGRESS}")


@pytest.mark.offline
@pytest.mark.asyncio
async def test_harvest_without_prior_checkpoint_leaves_store_unchanged(
    harvest_env, patch_residue_dir: Path, patch_admission_mode: None
) -> None:
    _record(store_dir=patch_residue_dir, skip_count=1)
    turns = [
        _ADMISSION,
        _turn(12, "CHECKPOINT wave 4", _checkpoint_with_footer(_BASE_CHECKPOINT)),
    ]
    await harvest_env.harvest_completed_windows("5870", turns)
    loaded = load_residue_record("5870", store_dir=patch_residue_dir)
    assert loaded is not None
    assert loaded.consecutive_skip_count == 1
