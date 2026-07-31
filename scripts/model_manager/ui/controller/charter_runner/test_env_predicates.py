"""Unit tests for charter ENV-half predicates (§5.3 registry + snapshot eval)."""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from implement_admission.dense_spec_schema import validate_dense_spec

from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
from scripts.model_manager.ui.controller.charter_runner.admission import (
    evaluate_root,
    live_wip_for_window,
)
from scripts.model_manager.ui.controller.charter_runner.env_predicates import (
    ADMIT_INTENT_ORPHAN_ID,
    ADMIT_INTENT_ORPHAN_REASON,
    ENV_PREDICATE_REGISTRY,
    ENV_SNAPSHOT_STALE_REASON,
    GIW_DRAIN_BLOCKS_RESTART_REASON,
    GIW_DRAIN_INTENT_ID,
    GIW_HOLD_BLOCKS_RESTART_REASON,
    GIW_LIVE_HOLD_ID,
    SOURCE_GIW_DRAIN,
    SOURCE_GIW_LIVE,
    EnvEvalContext,
    EnvironmentSnapshot,
    SourceRead,
    evaluate_env_half,
    registry_skip_reasons,
)
from scripts.model_manager.ui.controller.charter_runner.kernel import (
    maybe_heal_admit_intent_orphan,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_COMPLETED,
    STATUS_PENDING_DRAIN,
    Intent,
)

_REPO = Path(__file__).resolve().parents[5]
_ELIGIBILITY_PATH = (
    _REPO
    / "scripts/model_manager/ui/controller/charter_runner/admission/body_gate.py"
)


def _giw_intent(*, status: str = STATUS_PENDING_DRAIN) -> Intent:
    return Intent(
        intent_id="test-giw-intent",
        service="git_integration_worker",
        action="sync_restart",
        status=status,
        drain_epoch=1,
        worker_id="worker-1",
        worker_started_at="2026-01-01T00:00:00+00:00",
        deadline_at="2026-01-08T00:00:00+00:00",
        last_seen_event_seq=0,
        reason="manage deferred restart",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _snapshot(
    *,
    intent: Intent | None = None,
    giw_held: bool = False,
    connect_error: bool = False,
    error_read: bool = False,
    observed_at: datetime | None = None,
    ttl_s: float = 60.0,
) -> EnvironmentSnapshot:
    if connect_error:
        giw_read = SourceRead(
            status="degraded",
            payload=False,
            error_class="ConnectError",
            scope="tick",
        )
    elif error_read:
        giw_read = SourceRead(
            status="error",
            payload=True,
            error_class="TimeoutError",
            scope="tick",
        )
    else:
        giw_read = SourceRead(status="ok", payload=giw_held, scope="tick")
    return EnvironmentSnapshot(
        observed_at=observed_at or datetime.now(UTC),
        ttl_s=ttl_s,
        sources={
            SOURCE_GIW_LIVE: giw_read,
            SOURCE_GIW_DRAIN: SourceRead(status="ok", payload=intent, scope="tick"),
        },
    )


def _restart_turns(body: str) -> list[dict]:
    return [{"turn_number": 2, "subject": "CHECKPOINT wave 2", "body": body}]


_RESTART_BODY = """\
# CHECKPOINT

## Steps
1. [ ] G2 — manage sync_restart

## In-flight / WIP
none

## Next pickup
1. G2 — manage(sync_restart, service=git_integration_worker) → wait_healthy

## Frictions
_None this window._

## Sidecars
_None this window._

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""


@pytest.mark.offline
def test_e1_pending_drain_hold_on_restart_pickup() -> None:
    ctx = EnvEvalContext(restart_shaped=True, admit_intent_orphan=False)
    skip = evaluate_env_half(_snapshot(intent=_giw_intent()), ctx)
    assert skip is not None
    assert skip.reason == GIW_DRAIN_BLOCKS_RESTART_REASON
    assert skip.predicate_id == GIW_DRAIN_INTENT_ID


@pytest.mark.offline
def test_e1_null_intent_clear() -> None:
    ctx = EnvEvalContext(restart_shaped=True, admit_intent_orphan=False)
    assert evaluate_env_half(_snapshot(intent=None), ctx) is None


@pytest.mark.offline
def test_e1_terminal_intent_clear_unless_e2_holds() -> None:
    ctx = EnvEvalContext(restart_shaped=True, admit_intent_orphan=False)
    assert (
        evaluate_env_half(
            _snapshot(intent=_giw_intent(status=STATUS_COMPLETED)),
            ctx,
        )
        is None
    )


@pytest.mark.offline
def test_e1_sf1_terminal_intent_plus_e2_hold_still_hold() -> None:
    ctx = EnvEvalContext(restart_shaped=True, admit_intent_orphan=False)
    skip = evaluate_env_half(
        _snapshot(intent=_giw_intent(status=STATUS_COMPLETED), giw_held=True),
        ctx,
    )
    assert skip is not None
    assert skip.predicate_id == GIW_LIVE_HOLD_ID


@pytest.mark.offline
def test_e2_holder_hold_empty_clear() -> None:
    ctx = EnvEvalContext(restart_shaped=True, admit_intent_orphan=False)
    assert evaluate_env_half(_snapshot(giw_held=False), ctx) is None
    skip = evaluate_env_half(_snapshot(giw_held=True), ctx)
    assert skip is not None
    assert skip.reason == GIW_HOLD_BLOCKS_RESTART_REASON


@pytest.mark.offline
def test_e2_connect_error_clear() -> None:
    ctx = EnvEvalContext(restart_shaped=True, admit_intent_orphan=False)
    assert evaluate_env_half(_snapshot(connect_error=True), ctx) is None


@pytest.mark.offline
def test_e2_timeout_hold() -> None:
    ctx = EnvEvalContext(restart_shaped=True, admit_intent_orphan=False)
    skip = evaluate_env_half(_snapshot(error_read=True), ctx)
    assert skip is not None
    assert skip.predicate_id == GIW_LIVE_HOLD_ID


@pytest.mark.offline
def test_e7_orphan_intent_hold_without_wip() -> None:
    ctx = EnvEvalContext(restart_shaped=False, admit_intent_orphan=True)
    skip = evaluate_env_half(_snapshot(), ctx)
    assert skip is not None
    assert skip.reason == ADMIT_INTENT_ORPHAN_REASON
    assert skip.predicate_id == ADMIT_INTENT_ORPHAN_ID


@pytest.mark.offline
def test_e7_no_orphan_clear() -> None:
    ctx = EnvEvalContext(restart_shaped=False, admit_intent_orphan=False)
    assert evaluate_env_half(_snapshot(), ctx) is None


@pytest.mark.offline
def test_env_snapshot_stale_skips() -> None:
    stale_at = datetime.now(UTC) - timedelta(seconds=120)
    ctx = EnvEvalContext(restart_shaped=False, admit_intent_orphan=False)
    skip = evaluate_env_half(_snapshot(observed_at=stale_at), ctx)
    assert skip is not None
    assert skip.reason == ENV_SNAPSHOT_STALE_REASON


@pytest.mark.offline
def test_env_predicate_registry_matches_spec() -> None:
    expected = {
        GIW_DRAIN_INTENT_ID: ("obs", GIW_DRAIN_BLOCKS_RESTART_REASON),
        GIW_LIVE_HOLD_ID: ("res", GIW_HOLD_BLOCKS_RESTART_REASON),
        ADMIT_INTENT_ORPHAN_ID: ("obs", ADMIT_INTENT_ORPHAN_REASON),
        "env_snapshot_stale": ("obs", ENV_SNAPSHOT_STALE_REASON),
    }
    assert len(ENV_PREDICATE_REGISTRY) == len(expected)
    for row in ENV_PREDICATE_REGISTRY:
        cls, reason = expected[row.id]
        assert row.predicate_class == cls
        assert row.skip_reason == reason
        assert row.posture_justification.strip()


@pytest.mark.offline
def test_env_skip_reasons_are_registry_owned() -> None:
    env_reasons = {
        GIW_DRAIN_BLOCKS_RESTART_REASON,
        GIW_HOLD_BLOCKS_RESTART_REASON,
        ADMIT_INTENT_ORPHAN_REASON,
        ENV_SNAPSHOT_STALE_REASON,
    }
    assert env_reasons == registry_skip_reasons()


@pytest.mark.offline
def test_eligibility_imports_no_substrate_adapters() -> None:
    tree = ast.parse(_ELIGIBILITY_PATH.read_text(encoding="utf-8"))
    banned = {
        "giw_live_hold",
        "RestartIntentStore",
        "restart_intent_store",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for name in banned:
                assert name not in node.module
            for alias in node.names:
                assert alias.name not in banned
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in banned


@pytest.mark.offline
def test_reload_census_includes_env_modules() -> None:
    import pytest
    pytest.skip("Phase 3: reload.py deleted")



def test_evaluate_root_e7_orphan_via_env_half(tmp_path: Path) -> None:
    caps = CapStore(intent_dir=tmp_path / "intent")
    caps.mark_admit_intent("5555", 1)
    decision = evaluate_root(
        "5555",
        _restart_turns(_RESTART_BODY),
        caps,
        env_snapshot=_snapshot(),
    )
    assert decision.eligible is False
    assert decision.reason == ADMIT_INTENT_ORPHAN_REASON
    assert decision.half == "env"
    assert decision.predicate_id == ADMIT_INTENT_ORPHAN_ID


@pytest.mark.offline
def test_live_wip_for_window_blocks_orphan_heal_predicate() -> None:
    turns = _restart_turns(_RESTART_BODY) + [
        {
            "turn_number": 3,
            "subject": "WIP charter-runner window 1",
            "body": '{"window":1,"charter_runner":true}',
        }
    ]
    assert live_wip_for_window(turns, 1) is True
    assert live_wip_for_window(turns, 2) is False


@pytest.mark.offline
def test_orphan_intent_heal_retired_is_noop(tmp_path: Path) -> None:
    """Phase 3: admit-intent orphan heal is retired — always False, intent kept."""
    caps = CapStore(intent_dir=tmp_path / "intent")
    caps.mark_admit_intent("5555", 1)
    turns = _restart_turns(_RESTART_BODY)
    healed = asyncio.run(maybe_heal_admit_intent_orphan("5555", turns, caps))
    assert healed is False
    assert caps.has_admit_intent("5555", 1)


@pytest.mark.offline
def test_orphan_intent_no_heal_when_root_stopped(tmp_path: Path) -> None:
    """a:26167 / a:26168: 5xx keep-intent must survive heal (stopped root)."""
    caps = CapStore(intent_dir=tmp_path / "intent")
    caps.mark_admit_intent("5555", 1)
    caps.mark_failed("5555", "admission_transport_error")
    turns = _restart_turns(_RESTART_BODY)
    healed = asyncio.run(maybe_heal_admit_intent_orphan("5555", turns, caps))
    assert healed is False
    assert caps.has_admit_intent("5555", 1)


@pytest.mark.offline
def test_orphan_intent_no_heal_when_live_wip(tmp_path: Path) -> None:
    caps = CapStore(intent_dir=tmp_path / "intent")
    caps.mark_admit_intent("5555", 1)
    turns = _restart_turns(_RESTART_BODY) + [
        {
            "turn_number": 3,
            "subject": "WIP charter-runner window 1",
            "body": '{"window":1,"charter_runner":true,"posted_at":"2026-01-01T00:00:00+00:00"}',
        }
    ]
    healed = asyncio.run(maybe_heal_admit_intent_orphan("5555", turns, caps))
    assert healed is False
    assert caps.has_admit_intent("5555", 1)


@pytest.mark.offline
def test_orphan_intent_hold_when_worker_live(tmp_path: Path) -> None:
    caps = CapStore(intent_dir=tmp_path / "intent")
    caps.mark_admit_intent("5555", 1)
    caps.bind_intent_worker("5555", 1, "w1")

    async def _probe() -> bool:
        async def active_worker(_thread: str) -> None:
            return None

        async def fetch_active(_thread: str) -> dict:
            return {"status": "active"}

        import scripts.model_manager.ui.controller.charter_runner.bus_client as bc

        orig_failure = bc.worker_failure_reason
        orig_fetch = bc.fetch_thread
        bc.worker_failure_reason = active_worker  # type: ignore[assignment]
        bc.fetch_thread = fetch_active  # type: ignore[assignment]
        try:
            return await maybe_heal_admit_intent_orphan(
                "5555", _restart_turns(_RESTART_BODY), caps
            )
        finally:
            bc.worker_failure_reason = orig_failure
            bc.fetch_thread = orig_fetch

    assert asyncio.run(_probe()) is False
    assert caps.has_admit_intent("5555", 1)


@pytest.mark.offline
def test_evaluate_root_giw_drain_via_env_half() -> None:
    decision = evaluate_root(
        "5555",
        _restart_turns(_RESTART_BODY),
        CapStore(),
        env_snapshot=_snapshot(intent=_giw_intent()),
    )
    assert decision.eligible is False
    assert decision.reason == GIW_DRAIN_BLOCKS_RESTART_REASON
    assert decision.half == "env"


@pytest.mark.offline
def test_substrate_touching_requires_environment_preconditions_section() -> None:
    base = """\
# Dense spec

## Problem
p

## Non-goals
n

## Provenance
p

## Touch-points
t

## Bound design
f

## Implementation guidance
i

## Acceptance criteria
a

## Verification
v

substrate_touching: true

<reasoning_trace>
No fork remains open.
</reasoning_trace>
"""
    verdict = validate_dense_spec(base)
    assert verdict.passed is False
    assert "environment_preconditions" in verdict.missing_sections

    with_section = base.replace(
        "## Verification\nv",
        "## Verification\nv\n\n## Environment preconditions\n- E1 giw drain\n",
    )
    assert validate_dense_spec(with_section).passed is True
