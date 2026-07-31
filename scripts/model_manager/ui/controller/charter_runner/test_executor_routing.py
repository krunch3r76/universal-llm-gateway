"""Wave 2 executor routing — declared-token-first, fail-closed-to-judgment.

Covers the review's falsifier set (§10): a wrong Composer window puts a
mechanical executor on a judgment step, so every ambiguous or unproven input
must resolve to the Grok judgment bind.
"""

from __future__ import annotations

import pytest

from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.executor_defaults import (
    IMPLEMENT_MODEL,
    IMPLEMENT_MODEL_KNOBS,
    implement_body,
)
from scripts.model_manager.ui.controller.charter_runner.executor_routing import (
    IMPLEMENT_LANE,
    JUDGMENT_LANE,
    resolve_charter_executor,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec import (
    materialize_autonomous_packet,
)

_ANCHOR = "## Anchor\n- Todo: todo:widget-roundtrip · workflow_state=implement_ready\n"


def _checkpoint(pickup: str, *, anchor: str = _ANCHOR) -> str:
    return (
        f"# CHECKPOINT\n\n{anchor}\n"
        "## In-flight / WIP\nnone\n\n"
        f"## Next pickup\n1. {pickup}\n\n"
        "Scoreboard: cortex://notes/system/threads/5609-charter-scoreboard.md\n"
    )


def _resolve(
    pickup: str,
    *,
    anchor: str = _ANCHOR,
    mode: str = "autonomous",
    arc_lane: str = "path_sim",
):
    parsed = parse_checkpoint(_checkpoint(pickup, anchor=anchor))
    return resolve_charter_executor(
        parsed=parsed, admission_mode=mode, arc_lane=arc_lane
    )


@pytest.mark.offline
def test_declared_implement_lane_with_source_ref_routes_to_implement() -> None:
    bind = _resolve("G4 — land the bind · executor_lane: implement")
    assert bind.lane == IMPLEMENT_LANE
    assert bind.reason == "declared_implement"
    assert bind.source_ref == "todo:widget-roundtrip"


@pytest.mark.offline
def test_bare_g4_without_declaration_uses_heuristic() -> None:
    bind = _resolve("G4 — implement the admitted bind")
    assert bind.lane == IMPLEMENT_LANE
    assert bind.reason == "heuristic_g4"


@pytest.mark.offline
def test_declared_judgment_overrides_g4_heuristic() -> None:
    bind = _resolve("G4 — implement the bind · executor_lane: judgment")
    assert bind.lane == JUDGMENT_LANE
    assert bind.reason == "declared_judgment"
    assert bind.source_ref is None


@pytest.mark.offline
def test_pickup_naming_two_gated_classes_fails_closed() -> None:
    # Realistic shape: "G4 — implement the R-admitted bind (G3 ADMIT, turn 5767)".
    bind = _resolve("G4 — implement the R-admitted bind (G3 ADMIT, turn 5767)")
    assert bind.lane == JUDGMENT_LANE
    assert bind.reason == "ambiguous_gated_ids"


@pytest.mark.offline
def test_revise_row_stays_on_judgment_bind() -> None:
    # G4a is a probe-fail revise window: CHECKPOINT-only, no file change, so
    # contract=implement would label it degraded (review §6).
    bind = _resolve("G4a — revise after probe fail")
    assert bind.lane == JUDGMENT_LANE
    assert bind.reason == "no_implement_row"


@pytest.mark.offline
def test_implement_without_resolvable_source_ref_fails_closed() -> None:
    bind = _resolve(
        "G4 — land the bind · executor_lane: implement", anchor="## Anchor\n- no ref\n"
    )
    assert bind.lane == JUDGMENT_LANE
    assert bind.reason == "implement_source_ref_unresolved"


@pytest.mark.offline
def test_two_distinct_todo_refs_are_not_resolvable() -> None:
    anchor = "## Anchor\n- Todo: todo:alpha\n- Also: todo:beta\n"
    bind = _resolve("G4 — land it · executor_lane: implement", anchor=anchor)
    assert bind.lane == JUDGMENT_LANE
    assert bind.reason == "implement_source_ref_unresolved"


@pytest.mark.offline
def test_implement_source_ref_from_next_pickup_not_steps() -> None:
    """Open G7/G8 todos in Steps must not block G4 implement on another todo."""
    body = (
        f"# CHECKPOINT\n\n"
        "## Anchor\n- Todo: todo:cursor-auto-in-seat-nested-terminal\n\n"
        "## In-flight / WIP\nnone\n\n"
        "## Next pickup\n"
        "1. G4 — Stage-B implement · todo:cursor-auto-in-seat-nested-terminal · "
        "executor_lane: implement\n\n"
        "## Steps\n"
        "6. [ ] G6 — cursor-auto · todo:cursor-auto-in-seat-nested-terminal\n"
        "7. [ ] G7 — cdp-proxy · todo:cdp-proxy-spec-in-seat-holder-divergence\n"
        "8. [ ] G8 — gate trio · todo:git-worker-gate-serialization-trio\n"
    )
    parsed = parse_checkpoint(body)
    bind = resolve_charter_executor(
        parsed=parsed, admission_mode="autonomous", arc_lane="path_sim"
    )
    assert bind.lane == IMPLEMENT_LANE
    assert bind.source_ref == "todo:cursor-auto-in-seat-nested-terminal"


@pytest.mark.offline
def test_conflicting_declared_lanes_fail_closed() -> None:
    body = (
        f"# CHECKPOINT\n\n{_ANCHOR}\n## In-flight / WIP\nnone\n\n"
        "## Next pickup\n"
        "1. G4 — land the bind · executor_lane: implement\n"
        "2. G5 — R-after · executor_lane: judgment\n"
    )
    parsed = parse_checkpoint(body)
    assert parsed.executor_lane_ambiguous is True
    bind = resolve_charter_executor(
        parsed=parsed, admission_mode="autonomous", arc_lane="path_sim"
    )
    assert bind.lane == JUDGMENT_LANE
    assert bind.reason == "declared_lane_ambiguous"


@pytest.mark.offline
@pytest.mark.parametrize("mode", ["generate", "handoff", "consult"])
def test_non_autonomous_modes_never_route_to_implement(mode: str) -> None:
    bind = _resolve("G4 — land the bind · executor_lane: implement", mode=mode)
    assert bind.lane == JUDGMENT_LANE
    assert bind.reason == f"mode_{mode}"


@pytest.mark.offline
def test_consult_role_keeps_consult_seat_even_when_pickup_names_g4() -> None:
    parsed = parse_checkpoint(
        _checkpoint("G4 — CONSULT_PENDING · consult_role: r_admit")
    )
    bind = resolve_charter_executor(
        parsed=parsed, admission_mode="consult", consult_role="r_admit"
    )
    assert bind.lane == JUDGMENT_LANE


@pytest.mark.offline
def test_implement_packet_carries_front_matter_source_ref() -> None:
    parsed = parse_checkpoint(_checkpoint("G4 — land it · executor_lane: implement"))
    packet = materialize_autonomous_packet(
        "5609", parsed, window_index=4, source_ref="todo:widget-roundtrip"
    )
    # Without a leading --- region frontmatter_value returns None and
    # require_implement_ready short-circuits into a no-op.
    assert packet.startswith("---\nsource_ref: todo:widget-roundtrip\n---\n")


@pytest.mark.offline
def test_judgment_packet_has_no_front_matter() -> None:
    parsed = parse_checkpoint(_checkpoint("G5 — R-after"))
    packet = materialize_autonomous_packet("5609", parsed, window_index=5)
    assert packet.startswith("<scope>")


@pytest.mark.offline
def test_missing_implement_spec_hash_is_flagged_as_gate_bypass() -> None:
    from scripts.model_manager.ui.controller.charter_runner import dispatch_client as dc

    body = {"contract": "implement", "source_ref": "todo:widget-roundtrip"}
    result: dict = {"dispatch_id": "d1"}
    dc._warn_on_ungated_implement("5609", body, result)
    assert result["implement_gate_bypassed"] is True


@pytest.mark.offline
def test_stamped_implement_spec_hash_is_not_flagged() -> None:
    from scripts.model_manager.ui.controller.charter_runner import dispatch_client as dc

    body = {"contract": "implement", "source_ref": "todo:widget-roundtrip"}
    result: dict = {"dispatch_id": "d1", "implement_spec_hash": "sha256:abc"}
    dc._warn_on_ungated_implement("5609", body, result)
    assert "implement_gate_bypassed" not in result


@pytest.mark.offline
def test_judgment_window_is_never_gate_checked() -> None:
    from scripts.model_manager.ui.controller.charter_runner import dispatch_client as dc

    result: dict = {"dispatch_id": "d1"}
    dc._warn_on_ungated_implement("5609", {"contract": "light-bounded"}, result)
    assert "implement_gate_bypassed" not in result


@pytest.mark.offline
@pytest.mark.parametrize(
    ("fired_model", "other_model"),
    [("cursor/composer-2.5", "grok-4.5"), ("cursor/grok-4.5", "composer-2.5")],
)
@pytest.mark.skip(reason="Phase 3: admit.py deleted — port to window_exec")
def test_admit_notification_names_the_model_that_actually_fired(
    monkeypatch: pytest.MonkeyPatch, tmp_path, fired_model: str, other_model: str
) -> None:
    """Falsifier 9 — the admit message must never disagree with the fired body."""
    import asyncio
    from unittest.mock import AsyncMock

    from scripts.model_manager.ui.controller.charter_runner import admit
    from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
    from scripts.model_manager.ui.controller.charter_runner.admission import Decision

    parsed = parse_checkpoint(_checkpoint("G4 — land it · executor_lane: implement"))
    decision = Decision(
        True,
        "eligible",
        "5705",
        checkpoint={"turn_number": 1, "body": "# CHECKPOINT"},
        parsed=parsed,
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    messages: list[str] = []

    async def fake_fire(*_a, **_k) -> dict:
        return {
            "thread_id": "w1",
            "dispatch_id": "d1",
            "executor": {"model": fired_model},
        }

    monkeypatch.setattr(admit.dispatch_client, "fire_window", fake_fire)
    monkeypatch.setattr(admit.bus_client, "post_admission_pointer", AsyncMock())
    monkeypatch.setattr(admit.events, "emit_manage_charter_tick_admitted", AsyncMock())
    monkeypatch.setattr(admit.window_log, "append_admit", lambda **_k: None)
    monkeypatch.setattr(admit.window_log, "append_executor_note", lambda *_a: None)
    monkeypatch.setattr(admit, "select_packet", lambda *a, **k: ("packet", "subject"))

    ok = asyncio.run(
        admit.admit_window(
            decision=decision,
            turns=[],
            caps=CapStore(intent_dir=tmp_path / "intent"),
            workspace_root=workspace,
            on_admit=messages.append,
            admission_mode="autonomous",
        )
    )

    assert ok is True
    assert messages, "admit should notify"
    assert fired_model in messages[0]
    assert other_model not in messages[0]


@pytest.mark.offline
def test_implement_body_pins_fast_true_only() -> None:
    # Composer exposes only `fast`; pin true for iteration-speed bind.
    body = implement_body(
        root_id="5609",
        window_index=4,
        packet_path="tmp/charter-runner/5609-w4.md",
        subject="s",
        caller_agent="charter-runner",
        source_ref="todo:widget-roundtrip",
    )
    assert body["model"] == IMPLEMENT_MODEL
    assert body["model_knobs"] == {"fast": "true"}
    assert IMPLEMENT_MODEL_KNOBS == {"fast": "true"}
    assert body["contract"] == "implement"
    assert body["source_ref"] == "todo:widget-roundtrip"
