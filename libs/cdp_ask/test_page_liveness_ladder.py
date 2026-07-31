"""Hermetic tests for held-page dual-completion ladder advance (friction 25671)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cdp_ask.page_liveness import (
    ESCAPE_IDLE_SAMPLES,
    GROWTH_IDLE_SAMPLES,
    SUSTAINED_IDLE_SAMPLES,
    LadderAdvanceState,
    LadderCallbacks,
    advance_ladder_from_harvest,
    archive_stamp_allows_content_proof,
    make_harvest_ladder_hook,
    page_idle_from_state,
)
from cdp_ask.structural_quiet import STRUCTURAL_QUIET_SAMPLES

pytestmark = pytest.mark.offline

_EXEC_ID = "abc123def456"
_FOREIGN_ID = "foreign99exec"


def _idle_state(**overrides: object) -> dict:
    state = {
        "streaming": False,
        "stop": False,
        "tool_pause": False,
        "body_len": 100,
    }
    state.update(overrides)
    return state


def _active_state(**overrides: object) -> dict:
    return _idle_state(streaming=True, **overrides)


def _stamped_archive(path: Path, body: str, execution_id: str) -> None:
    path.write_text(
        f"# CDP ask harvest\n\n"
        f"- execution_id: `{execution_id}`\n\n"
        f"## Body\n\n{body}\n",
        encoding="utf-8",
    )


async def _drive_active_then_idle(
    callbacks: LadderCallbacks,
    progress: LadderAdvanceState,
    *,
    idle_samples: int,
    body_len: int = 100,
    baseline_body_len: int = 100,
) -> None:
    """Flip ``seen_active`` then feed *idle_samples* consecutive idle harvest samples."""
    await advance_ladder_from_harvest(
        _active_state(body_len=baseline_body_len),
        callbacks=callbacks,
        progress=progress,
    )
    for _ in range(idle_samples):
        await advance_ladder_from_harvest(
            _idle_state(body_len=body_len),
            callbacks=callbacks,
            progress=progress,
        )


async def _noop() -> None:
    return None


def test_page_idle_from_state_requires_all_quiet() -> None:
    assert page_idle_from_state(_idle_state())
    assert not page_idle_from_state(_idle_state(streaming=True))
    assert not page_idle_from_state(_idle_state(stop=True))
    assert not page_idle_from_state(_idle_state(tool_pause=True))


def test_idle_active_mutual_exclusion_on_seen_active_sample() -> None:
    """A sample that sets seen_active must never co-occur with idle=True (A3)."""
    for active_key in ("streaming", "stop", "tool_pause"):
        state = _idle_state(**{active_key: True})
        assert not page_idle_from_state(state)


def test_archive_stamp_allows_content_proof(tmp_path: Path) -> None:
    path = tmp_path / "gate.md"
    assert not archive_stamp_allows_content_proof(path, "")

    path.write_text("x" * 50, encoding="utf-8")
    assert not archive_stamp_allows_content_proof(path, _EXEC_ID)

    _stamped_archive(path, "x" * 50, _FOREIGN_ID)
    assert not archive_stamp_allows_content_proof(path, _EXEC_ID)

    _stamped_archive(path, "x" * 50, _EXEC_ID)
    assert archive_stamp_allows_content_proof(path, _EXEC_ID)


@pytest.mark.asyncio
async def test_advance_ladder_idle_then_content_proof(
    tmp_path: Path,
) -> None:
    sidecar = tmp_path / "review.md"
    _stamped_archive(sidecar, "x" * 250, _EXEC_ID)
    events: list[str] = []

    async def on_turn_idle() -> None:
        events.append("turn_idle")

    async def on_content_proof(uri: str, sha: str) -> None:
        events.append(f"content_proof:{uri}:{sha[:20]}")

    async def on_liveness(
        streaming: bool, stop: bool, tool_pause: bool, observed_at: float
    ) -> None:
        events.append(f"liveness:{streaming}:{stop}:{tool_pause}")

    progress = LadderAdvanceState(
        targets=[(sidecar, "cortex://notes/review.md")],
        min_bytes=200,
        sha256_file=lambda p: f"sha256:{p.read_bytes().hex()[:16]}",
        execution_id=_EXEC_ID,
    )
    callbacks = LadderCallbacks(
        on_turn_idle=on_turn_idle,
        on_content_proof=on_content_proof,
        on_liveness=on_liveness,
    )

    await advance_ladder_from_harvest(
        _active_state(body_len=100),
        callbacks=callbacks,
        progress=progress,
    )
    assert events == ["liveness:True:False:False"]
    assert not progress.turn_idle_sent

    for _ in range(GROWTH_IDLE_SAMPLES):
        await advance_ladder_from_harvest(
            _idle_state(body_len=350),
            callbacks=callbacks,
            progress=progress,
        )
    assert progress.turn_idle_sent
    assert progress.turn_idle_arm == "growth"
    assert progress.content_proof_sent
    assert "turn_idle" in events
    assert any(e.startswith("content_proof:") for e in events)


@pytest.mark.asyncio
async def test_pre_gen_quiet_does_not_fire_before_escape() -> None:
    """First idle samples without prior activity must not latch turn_idle (AC#1)."""
    fired: list[str] = []

    async def on_turn_idle() -> None:
        fired.append("turn_idle")

    progress = LadderAdvanceState(min_bytes=40)
    callbacks = LadderCallbacks(on_turn_idle=on_turn_idle)

    for streak in range(1, ESCAPE_IDLE_SAMPLES):
        await advance_ladder_from_harvest(
            _idle_state(), callbacks=callbacks, progress=progress
        )
        assert progress.idle_streak == streak
        assert not progress.turn_idle_sent
        assert fired == []

    await advance_ladder_from_harvest(
        _idle_state(), callbacks=callbacks, progress=progress
    )
    assert progress.turn_idle_sent
    assert progress.turn_idle_arm == "escape"
    assert fired == ["turn_idle"]


@pytest.mark.asyncio
async def test_growth_arm_requires_delta_and_debounce() -> None:
    fired: list[str] = []

    async def on_turn_idle() -> None:
        fired.append("turn_idle")

    progress = LadderAdvanceState(min_bytes=50)
    callbacks = LadderCallbacks(on_turn_idle=on_turn_idle)

    await advance_ladder_from_harvest(
        _active_state(body_len=100), callbacks=callbacks, progress=progress
    )
    assert progress.seen_active
    assert progress.body_len_baseline == 100

    await advance_ladder_from_harvest(
        _idle_state(body_len=200), callbacks=callbacks, progress=progress
    )
    assert not progress.turn_idle_sent
    assert progress.idle_streak == 1

    await advance_ladder_from_harvest(
        _idle_state(body_len=200), callbacks=callbacks, progress=progress
    )
    assert progress.turn_idle_sent
    assert progress.turn_idle_arm == "growth"
    assert fired == ["turn_idle"]


@pytest.mark.asyncio
async def test_sustained_arm_fires_below_growth_delta() -> None:
    fired: list[str] = []

    async def on_turn_idle() -> None:
        fired.append("turn_idle")

    progress = LadderAdvanceState(min_bytes=500)
    callbacks = LadderCallbacks(on_turn_idle=on_turn_idle)

    await _drive_active_then_idle(
        callbacks,
        progress,
        idle_samples=SUSTAINED_IDLE_SAMPLES - 1,
        body_len=120,
        baseline_body_len=100,
    )
    assert not progress.turn_idle_sent

    await advance_ladder_from_harvest(
        _idle_state(body_len=120), callbacks=callbacks, progress=progress
    )
    assert progress.turn_idle_sent
    assert progress.turn_idle_arm == "sustained"
    assert fired == ["turn_idle"]


@pytest.mark.asyncio
async def test_mid_generation_single_idle_does_not_latch() -> None:
    """Decisive F1 regression: one mid-run idle after growth must not latch (AC#10)."""
    fired: list[str] = []

    async def on_turn_idle() -> None:
        fired.append("turn_idle")

    progress = LadderAdvanceState(min_bytes=40)
    callbacks = LadderCallbacks(on_turn_idle=on_turn_idle)

    await advance_ladder_from_harvest(
        _idle_state(body_len=0), callbacks=callbacks, progress=progress
    )
    assert not progress.turn_idle_sent

    await advance_ladder_from_harvest(
        _active_state(body_len=10), callbacks=callbacks, progress=progress
    )
    assert progress.seen_active

    await advance_ladder_from_harvest(
        _active_state(body_len=80), callbacks=callbacks, progress=progress
    )

    await advance_ladder_from_harvest(
        _idle_state(body_len=80), callbacks=callbacks, progress=progress
    )
    assert not progress.turn_idle_sent
    assert fired == []

    await advance_ladder_from_harvest(
        _active_state(body_len=120), callbacks=callbacks, progress=progress
    )
    assert not progress.turn_idle_sent
    assert progress.idle_streak == 0


@pytest.mark.asyncio
async def test_content_proof_waits_for_sidecar_after_idle(
    tmp_path: Path,
) -> None:
    sidecar = tmp_path / "late.md"
    events: list[str] = []

    async def on_turn_idle() -> None:
        events.append("turn_idle")

    async def on_content_proof(uri: str, sha: str) -> None:
        events.append(f"content_proof:{uri}")

    progress = LadderAdvanceState(
        targets=[(sidecar, "cortex://notes/late.md")],
        min_bytes=10,
        sha256_file=lambda p: f"sha256:{p.stat().st_size}",
        execution_id=_EXEC_ID,
    )
    callbacks = LadderCallbacks(
        on_turn_idle=on_turn_idle,
        on_content_proof=on_content_proof,
    )

    await _drive_active_then_idle(
        callbacks, progress, idle_samples=SUSTAINED_IDLE_SAMPLES
    )
    assert events == ["turn_idle"]
    assert progress.turn_idle_sent
    assert not progress.content_proof_sent

    _stamped_archive(sidecar, "sidecar body here", _EXEC_ID)
    await advance_ladder_from_harvest(
        _idle_state(), callbacks=callbacks, progress=progress
    )
    assert events == ["turn_idle", "content_proof:cortex://notes/late.md"]
    assert progress.content_proof_sent


@pytest.mark.asyncio
async def test_foreign_stamp_blocks_content_proof(tmp_path: Path) -> None:
    sidecar = tmp_path / "foreign.md"
    _stamped_archive(sidecar, "x" * 250, _FOREIGN_ID)
    events: list[str] = []

    async def on_turn_idle() -> None:
        events.append("turn_idle")

    async def on_content_proof(uri: str, sha: str) -> None:
        events.append("content_proof")

    progress = LadderAdvanceState(
        targets=[(sidecar, "cortex://notes/foreign.md")],
        min_bytes=200,
        sha256_file=lambda p: "sha256:abc",
        execution_id=_EXEC_ID,
    )
    callbacks = LadderCallbacks(
        on_turn_idle=on_turn_idle,
        on_content_proof=on_content_proof,
    )

    await _drive_active_then_idle(
        callbacks, progress, idle_samples=SUSTAINED_IDLE_SAMPLES
    )
    assert events == ["turn_idle"]
    assert progress.turn_idle_sent
    assert not progress.content_proof_sent


@pytest.mark.asyncio
async def test_unstamped_occupied_file_blocks_content_proof(
    tmp_path: Path,
) -> None:
    sidecar = tmp_path / "unstamped.md"
    sidecar.write_text("x" * 250, encoding="utf-8")
    events: list[str] = []

    async def on_content_proof(uri: str, sha: str) -> None:
        events.append("content_proof")

    progress = LadderAdvanceState(
        targets=[(sidecar, "cortex://notes/unstamped.md")],
        min_bytes=200,
        sha256_file=lambda p: "sha256:abc",
        execution_id=_EXEC_ID,
    )
    callbacks = LadderCallbacks(
        on_turn_idle=_noop,
        on_content_proof=on_content_proof,
    )

    await _drive_active_then_idle(
        callbacks, progress, idle_samples=SUSTAINED_IDLE_SAMPLES
    )
    assert events == []
    assert progress.content_proof_sent is False


@pytest.mark.asyncio
async def test_empty_execution_id_blocks_content_proof(tmp_path: Path) -> None:
    sidecar = tmp_path / "empty-id.md"
    _stamped_archive(sidecar, "x" * 250, _EXEC_ID)
    events: list[str] = []

    async def on_content_proof(uri: str, sha: str) -> None:
        events.append("content_proof")

    progress = LadderAdvanceState(
        targets=[(sidecar, "cortex://notes/empty-id.md")],
        min_bytes=200,
        sha256_file=lambda p: "sha256:abc",
        execution_id="",
    )
    callbacks = LadderCallbacks(
        on_turn_idle=_noop,
        on_content_proof=on_content_proof,
    )

    await _drive_active_then_idle(
        callbacks, progress, idle_samples=SUSTAINED_IDLE_SAMPLES
    )
    assert events == []
    assert progress.content_proof_sent is False


@pytest.mark.asyncio
async def test_stamp_then_proof_sequence_after_foreign_skip(
    tmp_path: Path,
) -> None:
    sidecar = tmp_path / "sequence.md"
    _stamped_archive(sidecar, "x" * 250, _FOREIGN_ID)
    events: list[str] = []

    async def on_turn_idle() -> None:
        events.append("turn_idle")

    async def on_content_proof(uri: str, sha: str) -> None:
        events.append(f"content_proof:{uri}")

    progress = LadderAdvanceState(
        targets=[(sidecar, "cortex://notes/sequence.md")],
        min_bytes=200,
        sha256_file=lambda p: "sha256:abc",
        execution_id=_EXEC_ID,
    )
    callbacks = LadderCallbacks(
        on_turn_idle=on_turn_idle,
        on_content_proof=on_content_proof,
    )

    await _drive_active_then_idle(
        callbacks, progress, idle_samples=SUSTAINED_IDLE_SAMPLES
    )
    assert events == ["turn_idle"]
    assert not progress.content_proof_sent

    _stamped_archive(sidecar, "y" * 250, _EXEC_ID)
    await advance_ladder_from_harvest(
        _idle_state(), callbacks=callbacks, progress=progress
    )
    assert events == ["turn_idle", "content_proof:cortex://notes/sequence.md"]
    assert progress.content_proof_sent


@pytest.mark.asyncio
async def test_make_harvest_ladder_hook_binds_progress(
    tmp_path: Path,
) -> None:
    sidecar = tmp_path / "hook.md"
    _stamped_archive(sidecar, "enough bytes for min", _EXEC_ID)
    seen: list[str] = []

    async def on_turn_idle() -> None:
        seen.append("turn_idle")

    async def on_content_proof(uri: str, sha: str) -> None:
        seen.append(uri)

    progress = LadderAdvanceState(
        targets=[(sidecar, "cortex://hook.md")],
        min_bytes=5,
        sha256_file=lambda p: "sha256:abc",
        execution_id=_EXEC_ID,
    )
    hook = make_harvest_ladder_hook(
        callbacks=LadderCallbacks(
            on_turn_idle=on_turn_idle,
            on_content_proof=on_content_proof,
        ),
        progress=progress,
    )
    await hook(_active_state(body_len=100))
    for _ in range(SUSTAINED_IDLE_SAMPLES):
        await hook(_idle_state())
    assert "turn_idle" in seen
    assert "cortex://hook.md" in seen


@pytest.mark.asyncio
async def test_content_proof_requires_prior_turn_idle_sent(
    tmp_path: Path,
) -> None:
    """content_proof coupling unchanged — needs turn_idle_sent first (AC#5)."""
    sidecar = tmp_path / "coupled.md"
    _stamped_archive(sidecar, "x" * 250, _EXEC_ID)
    proof_events: list[str] = []

    async def on_content_proof(uri: str, sha: str) -> None:
        proof_events.append(uri)

    progress = LadderAdvanceState(
        targets=[(sidecar, "cortex://notes/coupled.md")],
        min_bytes=200,
        sha256_file=lambda p: "sha256:abc",
        execution_id=_EXEC_ID,
    )
    callbacks = LadderCallbacks(
        on_turn_idle=None,
        on_content_proof=on_content_proof,
    )

    await advance_ladder_from_harvest(
        _idle_state(), callbacks=callbacks, progress=progress
    )
    assert not progress.turn_idle_sent
    assert proof_events == []


def _latched_quiet_state(**overrides: object) -> dict:
    state = {
        "streaming": True,
        "stop": True,
        "tool_pause": False,
        "body_len": 500,
        "n": 1,
        "task_map_present": True,
        "task_map_idle": True,
        "task_map_working": False,
    }
    state.update(overrides)
    return state


@pytest.fixture
def structural_quiet_n(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cdp_ask.structural_quiet.STRUCTURAL_QUIET_SAMPLES", 5)


@pytest.mark.asyncio
async def test_structural_quiet_ladder_emits_arm_and_content_proof_unchanged(
    tmp_path: Path,
    structural_quiet_n,
) -> None:
    """Ladder fires turn_idle_arm=structural_quiet; archive stamp gate unchanged (AC7)."""
    sidecar = tmp_path / "sq.md"
    _stamped_archive(sidecar, "x" * 250, _EXEC_ID)
    events: list[str] = []

    async def on_turn_idle() -> None:
        events.append("turn_idle")

    async def on_content_proof(uri: str, sha: str) -> None:
        events.append(f"content_proof:{uri}")

    progress = LadderAdvanceState(
        targets=[(sidecar, "cortex://notes/sq.md")],
        min_bytes=50,
        sha256_file=lambda p: "sha256:abc",
        execution_id=_EXEC_ID,
    )
    callbacks = LadderCallbacks(
        on_turn_idle=on_turn_idle,
        on_content_proof=on_content_proof,
    )

    await advance_ladder_from_harvest(
        _active_state(body_len=100),
        callbacks=callbacks,
        progress=progress,
    )
    assert progress.seen_active
    assert progress.body_len_baseline == 100

    quiet = _latched_quiet_state(body_len=200)
    for _ in range(STRUCTURAL_QUIET_SAMPLES):
        await advance_ladder_from_harvest(quiet, callbacks=callbacks, progress=progress)

    assert progress.turn_idle_sent
    assert progress.turn_idle_arm == "structural_quiet"
    assert events == ["turn_idle"]
    assert not progress.content_proof_sent

    await advance_ladder_from_harvest(
        _idle_state(body_len=200), callbacks=callbacks, progress=progress
    )
    assert progress.content_proof_sent
    assert events[-1].startswith("content_proof:")
