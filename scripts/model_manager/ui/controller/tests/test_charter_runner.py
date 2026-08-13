"""Offline tests for the charter-runner tick, parser, eligibility, and caps."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts.model_manager.ui.controller.charter_runner import (
    dispatch_client as _dc_mod,
)
from scripts.model_manager.ui.controller.charter_runner import kernel as tl
from scripts.model_manager.ui.controller.charter_runner.admission import CapStore, WindowCaps
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    first_actionable_step,
    item_is_gated,
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.admission import (
    evaluate_root,
    next_pickup_is_restart_from_holder,
)
from scripts.model_manager.ui.controller.charter_runner.env_predicates import (
    GIW_DRAIN_BLOCKS_RESTART_REASON,
    SOURCE_GIW_DRAIN,
    SOURCE_GIW_LIVE,
    EnvironmentSnapshot,
    SourceRead,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec import (
    materialize_resume_packet,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_COMPLETED,
    STATUS_PENDING_DRAIN,
    Intent,
)
from scripts.model_manager.ui.controller.shutdown_gate import ManageShutdownGate
from scripts.model_manager.ui.model.service_state import ServiceInfo, ServiceStatus

_CHECKPOINT_BODY = """\
# CHECKPOINT wave 2 — agent-bus:5555

## Steps
1. [x] Recon complete
2. [ ] Implement parser
3. [ ] Wire loop

## In-flight / WIP
none

## Next pickup
1. G2 — implement parser
2. G3 — wire loop

## Frictions
_None this window._

## Sidecars
- cortex://notes/system/specs/foo.md — spec

Scoreboard: cortex://notes/system/threads/5555-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""


def _turn(n: int, subject: str, body: str = "") -> dict[str, Any]:
    return {"turn_number": n, "subject": subject, "body": body, "from_agent": "cursor"}


def _checkpoint_turns() -> list[dict[str, Any]]:
    return [
        _turn(1, "WIP 0.1 kickoff"),
        _turn(2, "CHECKPOINT wave 2", _CHECKPOINT_BODY),
    ]


# ---- parser --------------------------------------------------------------


@pytest.mark.offline
def test_parse_checkpoint_fields() -> None:
    parsed = parse_checkpoint(_CHECKPOINT_BODY)
    assert parsed.wip_is_none is True
    assert parsed.next_pickup_gated is True
    assert parsed.blocked is False
    assert parsed.open_operator_fork is False
    assert parsed.has_resume_footer is True
    assert parsed.scoreboard_uri == (
        "cortex://notes/system/threads/5555-charter-scoreboard.md"
    )
    assert [s.status for s in parsed.steps] == ["done", "pending", "pending"]
    step = first_actionable_step(parsed)
    assert step is not None and step.ordinal == 2


@pytest.mark.offline
def test_parse_wip_active() -> None:
    body = _CHECKPOINT_BODY.replace(
        "## In-flight / WIP\nnone", "## In-flight / WIP\nWIP(cursor): implementing"
    )
    assert parse_checkpoint(body).wip_is_none is False


@pytest.mark.offline
def test_parse_wip_none_with_parenthetical_gloss() -> None:
    """Dogfood CHECKPOINTs often write ``none (R1a about to dispatch)``."""
    body = _CHECKPOINT_BODY.replace(
        "## In-flight / WIP\nnone",
        "## In-flight / WIP\nnone (R1a about to dispatch)",
    )
    assert parse_checkpoint(body).wip_is_none is True


@pytest.mark.offline
def test_parse_wip_none_schema_silence_marker() -> None:
    """Autonomous workers write ``_None this window._`` under In-flight / WIP."""
    body = _CHECKPOINT_BODY.replace(
        "## In-flight / WIP\nnone",
        "## In-flight / WIP\n_None this window._",
    )
    parsed = parse_checkpoint(body)
    assert parsed.wip_is_none is True
    assert parsed.wip_text == "_None this window._"


@pytest.mark.offline
def test_parse_wip_none_silence_marker_with_parenthetical_gloss() -> None:
    """Friction 26060: schema silence + gloss must stay WIP-none (5686#16 live).

    Autonomous workers write ``_None this window._ (reason)``; prior
    ``_NONE_WINDOW_RE`` exact-match left ``wip_is_none=False`` → permanent
    ``wip_active`` starve. Parity with bare ``none (gloss)`` via shared helper.
    """
    gloss = "_None this window._ (exiting so drain can complete)"
    body = _CHECKPOINT_BODY.replace(
        "## In-flight / WIP\nnone",
        f"## In-flight / WIP\n{gloss}",
    )
    parsed = parse_checkpoint(body)
    assert parsed.wip_is_none is True
    assert parsed.wip_text == gloss
    # Precedents/Frictions silence + gloss also empties via shared helper.
    body_prec = (
        _CHECKPOINT_BODY
        + f"\n## Precedents\n{gloss}\n"
        + f"\n## Implications\n{gloss}\n"
    )
    parsed_prec = parse_checkpoint(body_prec)
    assert parsed_prec.precedents == []
    assert parsed_prec.implications == []
    # Multi-line WIP remains non-none (single-line guard).
    multi = _CHECKPOINT_BODY.replace(
        "## In-flight / WIP\nnone",
        f"## In-flight / WIP\n{gloss}\n- leftover bullet",
    )
    assert parse_checkpoint(multi).wip_is_none is False


@pytest.mark.offline
def test_parse_r_prefix_next_pickup_is_gated() -> None:
    """Charter beats use R1a/R1b; gated pickup must not require G-only ids."""
    body = _CHECKPOINT_BODY.replace(
        "1. G2 — implement parser", "1. R1b — Grok apply R1a amendments"
    )
    body = body.replace("2. G3 — wire loop", "2. R2a — CDP Opus review 5458")
    parsed = parse_checkpoint(body)
    assert parsed.next_pickup_gated is True
    decision = evaluate_root("5462", [_turn(2, "CHECKPOINT R1b", body)], CapStore())
    assert decision.eligible is True


@pytest.mark.offline
def test_parse_precedents_implications_present() -> None:
    body = (
        _CHECKPOINT_BODY
        + "\n## Precedents\n"
        "- [P1] [evidence: assertion:1] precedent claim\n"
        "\n## Implications\n"
        "- P1 ⇒ Steps: advance gated item G2\n"
    )
    parsed = parse_checkpoint(body)
    assert parsed.precedents == [
        "[P1] [evidence: assertion:1] precedent claim"
    ]
    assert parsed.implications == ["P1 ⇒ Steps: advance gated item G2"]


@pytest.mark.offline
def test_parse_precedents_implications_none_marker() -> None:
    body = (
        _CHECKPOINT_BODY
        + "\n## Precedents\n_None this window._\n"
        + "\n## Implications\n_None this window._\n"
    )
    parsed = parse_checkpoint(body)
    assert parsed.precedents == []
    assert parsed.implications == []


@pytest.mark.offline
def test_s1_implication_prefers_gated_next_pickup_over_first_step() -> None:
    """S1: resolvable Implication steers work away from first_actionable_step."""
    from scripts.model_manager.ui.controller.charter_runner.window_exec import (
        _work_summary,
    )

    body = (
        _CHECKPOINT_BODY
        + "\n## Precedents\n_None this window._\n"
        + "\n## Implications\n"
        "- P1 ⇒ Next-pickup: promote G3 ahead of G4\n"
    )
    parsed = parse_checkpoint(body)
    # First actionable step is still G2 / "Implement parser" (ordinal 2).
    step = first_actionable_step(parsed)
    assert step is not None and "Implement parser" in step.title
    work = _work_summary(parsed)
    assert "G3" in work
    assert "wire loop" in work


@pytest.mark.offline
def test_s1_unresolvable_implication_falls_back_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.model_manager.ui.controller.charter_runner.window_exec import (
        _work_summary,
    )

    body = (
        _CHECKPOINT_BODY
        + "\n## Implications\n"
        "- P1 ⇒ Next-pickup: promote T1 tangent without gated id\n"
        "- P2 ⇒ Steps: advance gated item G99 missing\n"
    )
    parsed = parse_checkpoint(body)
    with caplog.at_level("WARNING"):
        work = _work_summary(parsed)
    assert "implication_target_unresolved" in caplog.text
    assert "Implement parser" in work  # first_actionable_step fallback


@pytest.mark.offline
def test_resume_footer_requires_canonical_prefix() -> None:
    body = _CHECKPOINT_BODY.replace(
        "— RESUME (any seat, no command):",
        "RESUME (any seat, no command):",
    )
    assert parse_checkpoint(body).has_resume_footer is False
    assert parse_checkpoint(_CHECKPOINT_BODY).has_resume_footer is True


@pytest.mark.offline
def test_reload_module_deleted_phase3() -> None:
    """P3-AC4: in-process reload census module is gone; loop class is tick_loop."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(
            "scripts.model_manager.ui.controller.charter_runner.reload"
        )
    from scripts.model_manager.ui.controller.charter_runner.kernel import (
        CharterRunnerTickLoop,
    )

    assert CharterRunnerTickLoop.__name__ == "CharterRunnerTickLoop"


@pytest.mark.offline
def test_parse_blocked_step() -> None:
    body = _CHECKPOINT_BODY.replace(
        "2. [ ] Implement parser", "2. [!] Implement parser"
    )
    assert parse_checkpoint(body).blocked is True


@pytest.mark.offline
def test_parse_operator_fork_in_next_pickup() -> None:
    body = _CHECKPOINT_BODY.replace(
        "1. G2 — implement parser", "1. Operator: decide schema shape"
    )
    parsed = parse_checkpoint(body)
    assert parsed.open_operator_fork is True


# ---- eligibility ---------------------------------------------------------


@pytest.mark.offline
def test_eligible_when_idle_with_gated_pickup() -> None:
    decision = evaluate_root("5555", _checkpoint_turns(), CapStore())
    assert decision.eligible is True
    assert decision.reason == "eligible"


@pytest.mark.offline
def test_in_flight_when_admission_after_checkpoint() -> None:
    turns = _checkpoint_turns()
    turns.append(_turn(3, "WIP charter-runner window 1", '{"posted_at": "x"}'))
    decision = evaluate_root("5555", turns, CapStore())
    assert decision.eligible is False
    assert decision.reason == "window_in_flight"


@pytest.mark.offline
def test_no_checkpoint_skips() -> None:
    turns = [_turn(1, "WIP 0.1 kickoff")]
    assert evaluate_root("5555", turns, CapStore()).reason == "no_checkpoint"


@pytest.mark.offline
def test_no_gated_pickup_skips() -> None:
    body = _CHECKPOINT_BODY.replace("1. G2 — implement parser", "1. finish the thing")
    body = body.replace("2. G3 — wire loop", "2. also this")
    turns = [_turn(2, "CHECKPOINT wave 2", body)]
    assert evaluate_root("5555", turns, CapStore()).reason == "no_gated_pickup"


# ---- closeout admission grammar (a:26092 / G2 A1–A5) ----------------------


@pytest.mark.offline
def test_item_is_gated_closeout_allowlist_case_sensitive() -> None:
    assert item_is_gated("G6 — R-after") is True
    assert item_is_gated("R-after") is False
    assert item_is_gated("CLOSEOUT") is True
    assert item_is_gated("CLOSEOUT — friction_close") is True
    assert item_is_gated("arc-close") is True
    assert item_is_gated("arc_close") is True
    assert item_is_gated("closeout") is False
    assert item_is_gated("closeout — friction_close") is False


@pytest.mark.offline
def test_closeout_next_pickup_g6_gated() -> None:
    turns = _checkpoint_with_next_pickup("G6 — R-after")
    decision = evaluate_root("5555", turns, CapStore())
    parsed = parse_checkpoint(turns[0]["body"])
    assert parsed.next_pickup_gated is True
    assert decision.eligible is True
    assert decision.reason != "no_gated_pickup"
    assert decision.reason == "eligible"


@pytest.mark.offline
def test_closeout_next_pickup_allowlist_closeout_gated() -> None:
    gated = _checkpoint_with_next_pickup("CLOSEOUT — friction_close")
    assert parse_checkpoint(gated[0]["body"]).next_pickup_gated is True
    assert evaluate_root("5555", gated, CapStore()).reason != "no_gated_pickup"

    ungated = _checkpoint_with_next_pickup("closeout — friction_close")
    assert parse_checkpoint(ungated[0]["body"]).next_pickup_gated is False
    assert evaluate_root("5555", ungated, CapStore()).reason == "no_gated_pickup"


@pytest.mark.offline
def test_closeout_next_pickup_bare_r_after_ungated() -> None:
    turns = _checkpoint_with_next_pickup("R-after")
    parsed = parse_checkpoint(turns[0]["body"])
    assert parsed.next_pickup_gated is False
    assert evaluate_root("5555", turns, CapStore()).reason == "no_gated_pickup"


@pytest.mark.offline
def test_materializer_emits_closeout_next_pickup_marker() -> None:
    from scripts.model_manager.ui.controller.charter_runner.window_exec import (
        materialize_autonomous_packet,
    )

    parsed = parse_checkpoint(_CHECKPOINT_BODY)
    packet = materialize_autonomous_packet(
        "5555", parsed, scoreboard_uri=parsed.scoreboard_uri, window_index=1
    )
    assert "[closeout-next-pickup]" in packet
    assert "G6 — R-after" in packet


@pytest.mark.offline
def test_caps_stop_blocks_admission() -> None:
    caps = CapStore()
    caps.mark_failed("5555", "stale_window")
    decision = evaluate_root("5555", _checkpoint_turns(), caps)
    assert decision.eligible is False
    assert decision.reason.startswith("stopped")


def _giw_restart_intent(*, status: str = STATUS_PENDING_DRAIN) -> Intent:
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


def _checkpoint_with_next_pickup(*lines: str) -> list[dict[str, Any]]:
    body = _CHECKPOINT_BODY
    marker = "## Next pickup"
    start = body.index(marker)
    end = body.index("\n\n## Frictions", start)
    replacement = marker + "\n" + "\n".join(f"{idx}. {line}" for idx, line in enumerate(lines, 1))
    body = body[:start] + replacement + body[end:]
    return [_turn(2, "CHECKPOINT wave 2", body)]


def _env_snapshot(
    *,
    intent: Intent | None = None,
    giw_held: bool = False,
) -> EnvironmentSnapshot:
    from datetime import UTC, datetime

    return EnvironmentSnapshot(
        observed_at=datetime.now(UTC),
        ttl_s=60.0,
        sources={
            SOURCE_GIW_LIVE: SourceRead(status="ok", payload=giw_held, scope="tick"),
            SOURCE_GIW_DRAIN: SourceRead(status="ok", payload=intent, scope="tick"),
        },
    )


@pytest.mark.offline
def test_giw_drain_blocks_restart_shaped_next_pickup() -> None:
    turns = _checkpoint_with_next_pickup(
        "G2 — deploy-verify: manage(sync_restart, service=git_integration_worker) "
        "→ wait_healthy → live probe"
    )
    decision = evaluate_root(
        "5555",
        turns,
        CapStore(),
        env_snapshot=_env_snapshot(intent=_giw_restart_intent()),
    )
    assert decision.eligible is False
    assert decision.reason == GIW_DRAIN_BLOCKS_RESTART_REASON


@pytest.mark.offline
def test_giw_drain_allows_probe_only_next_pickup() -> None:
    turns = _checkpoint_with_next_pickup(
        "G2 — live probe after healthy (git_integration_worker)"
    )
    decision = evaluate_root(
        "5555",
        turns,
        CapStore(),
        env_snapshot=_env_snapshot(intent=_giw_restart_intent()),
    )
    assert decision.eligible is True
    assert decision.reason == "eligible"


@pytest.mark.offline
def test_giw_drain_no_live_intent_allows_restart_shaped_pickup() -> None:
    turns = _checkpoint_with_next_pickup(
        "G2 — deploy-verify: manage(sync_restart, service=git_integration_worker)"
    )
    decision = evaluate_root(
        "5555",
        turns,
        CapStore(),
        env_snapshot=_env_snapshot(intent=None),
    )
    assert decision.eligible is True
    assert decision.reason == "eligible"


@pytest.mark.offline
def test_giw_drain_terminal_intent_does_not_block_restart_pickup() -> None:
    turns = _checkpoint_with_next_pickup(
        "G2 — deploy-verify: manage(sync_restart, service=git_integration_worker)"
    )
    decision = evaluate_root(
        "5555",
        turns,
        CapStore(),
        env_snapshot=_env_snapshot(
            intent=_giw_restart_intent(status=STATUS_COMPLETED)
        ),
    )
    assert decision.eligible is True
    assert decision.reason == "eligible"


@pytest.mark.offline
def test_next_pickup_restart_from_holder_patterns() -> None:
    assert next_pickup_is_restart_from_holder(
        "manage(sync_restart, service=git_integration_worker) → wait_healthy"
    )
    assert not next_pickup_is_restart_from_holder(
        "live probe after healthy (git_integration_worker)"
    )
    assert not next_pickup_is_restart_from_holder(
        "manage(sync_restart, service=cortex_api)"
    )


# ---- caps ----------------------------------------------------------------


@pytest.mark.offline
def test_caps_consecutive_and_hourly() -> None:
    caps = CapStore(WindowCaps(max_consecutive=2, max_per_hour=5))
    assert caps.check("r")[0] is True
    caps.record_admit("r", now=1000.0)
    caps.record_admit("r", now=1001.0)
    ok, reason = caps.check("r", now=1002.0)
    assert ok is False and reason == "cap_consecutive"


@pytest.mark.offline
def test_caps_per_hour_window() -> None:
    caps = CapStore(WindowCaps(max_consecutive=100, max_per_hour=2))
    caps.record_admit("r", now=0.0)
    caps.record_admit("r", now=10.0)
    assert caps.check("r", now=20.0) == (False, "cap_per_hour")
    # Old admits age out of the 1h window.
    assert caps.check("r", now=4000.0)[0] is True


# ---- materializer --------------------------------------------------------


@pytest.mark.offline
def test_materializer_contains_stop_contract() -> None:
    parsed = parse_checkpoint(_CHECKPOINT_BODY)
    packet = materialize_resume_packet(
        "5555", parsed, scoreboard_uri=parsed.scoreboard_uri, window_index=3
    )
    assert "agent-bus:5555" in packet
    assert "<scope>" in packet
    assert "<task_guidance>" in packet
    assert "## Frictions" in packet
    assert "## Sidecars" in packet
    assert "## Steps" in packet
    assert "## Acceptance criteria" in packet
    assert "window 3" in packet
    assert "5555-charter-scoreboard.md" in packet
    assert "cursor/grok-4.6" in packet
    assert "from=cursor-sdk" in packet


@pytest.mark.offline
def test_default_judgment_body_is_grok_high() -> None:
    from scripts.model_manager.ui.controller.charter_runner.executor_defaults import (
        DEFAULT_MODEL,
        DEFAULT_MODEL_KNOBS,
        default_judgment_body,
    )

    body = default_judgment_body(
        root_id="5361",
        window_index=1,
        packet_path="universal-llm-gateway/tmp/charter-runner/5361-w1.md",
        subject="test",
        caller_agent="charter-runner",
    )
    assert body["op"] == "generate"
    assert body["seat"] == "cursor-sdk"
    assert body["model"] == DEFAULT_MODEL == "cursor/grok-4.6"
    assert body["model_knobs"] == DEFAULT_MODEL_KNOBS
    assert body["model_knobs"]["effort"] == "high"
    assert body["model_knobs"]["fast"] == "false"
    assert "thinking" not in body["model_knobs"]  # Grok has no thinking knob
    assert body["contract"] == "light-bounded"
    assert body["dispatch_thread_id"] == "5361"
    assert body["caller_agent"] == "charter-runner"
    # generate schema: subject/tags are handoff-only (Stargate 400 otherwise)
    assert "subject" not in body
    assert "tags" not in body


@pytest.mark.offline
def test_default_handoff_body_is_cursor_consult() -> None:
    from scripts.model_manager.ui.controller.charter_runner.executor_defaults import (
        default_handoff_body,
    )

    body = default_handoff_body(
        root_id="5361",
        window_index=2,
        packet_path="universal-llm-gateway/tmp/charter-runner/5361-w2.md",
        subject="attended test",
        caller_agent="charter-runner",
    )
    assert body["op"] == "handoff"
    assert body["role"] == "cursor-consult"
    assert body["packet_path"] == "universal-llm-gateway/tmp/charter-runner/5361-w2.md"
    assert body["subject"] == "attended test"
    assert body["caller_agent"] == "charter-runner"
    assert "admission:handoff" in body["tags"]
    assert "root:5361" in body["tags"]
    assert "window:2" in body["tags"]
    assert "seat" not in body
    assert "model" not in body


@pytest.mark.offline
def test_materializer_handoff_uses_from_cursor() -> None:
    parsed = parse_checkpoint(_CHECKPOINT_BODY)
    packet = materialize_resume_packet(
        "5555",
        parsed,
        scoreboard_uri=parsed.scoreboard_uri,
        window_index=1,
        admission_mode="handoff",
    )
    assert "from=cursor" in packet
    assert "from=cursor-sdk" not in packet
    assert "Attended substrate" in packet
    assert "open Multitask/IDE" in packet


# ---- autonomous materializer variant ------------------------------------


@pytest.mark.offline
def test_autonomous_packet_contains_background_lead_mandate() -> None:
    from scripts.model_manager.ui.controller.charter_runner.window_exec import (
        materialize_autonomous_packet,
    )

    parsed = parse_checkpoint(_CHECKPOINT_BODY)
    packet = materialize_autonomous_packet(
        "5555", parsed, scoreboard_uri=parsed.scoreboard_uri, window_index=4
    )
    # Six blocks present.
    assert "<scope>" in packet and "<task_guidance>" in packet
    assert "<mcp_capabilities>" in packet and "<output_format>" in packet
    assert "agent-bus:5555" in packet and "window 4" in packet
    # Full-arc background-lead mandate.
    assert "background lead" in packet
    assert "autonomous" in packet
    # Consult-hosted R-admit (holder posts CONSULT_PENDING; consult seat fires).
    assert "consult_role: r_admit" in packet
    assert "CONSULT_PENDING" in packet
    assert "IF6" in packet
    assert "project_ask(op=submit" not in packet
    # restart-auth loop, manage-only.
    assert "restart-auth" in packet
    assert "sync_restart" in packet
    assert "quality_gate" in packet
    assert "never systemctl / pkill / docker" in packet
    # Capped revise loop (default 3) modeled as clean CHECKPOINT.
    assert "revise" in packet.lower()
    assert "cap=3" in packet or "3 cycles" in packet
    assert "BLOCKED" in packet
    assert "R-verdict-gate" in packet
    assert "revise-counter" in packet
    assert "probe-vs-crash" in packet
    # Window boundary preserved.
    assert "exactly one CHECKPOINT" in packet
    # Mandatory CHECKPOINT sections + friction agent.
    assert "## Frictions" in packet and "## Sidecars" in packet and "## Steps" in packet
    assert "from=cursor-sdk" in packet


@pytest.mark.offline
def test_autonomous_packet_honors_custom_revise_cap() -> None:
    from scripts.model_manager.ui.controller.charter_runner.window_exec import (
        REVISE_CAP_DEFAULT,
        materialize_autonomous_packet,
    )

    assert REVISE_CAP_DEFAULT == 3
    parsed = parse_checkpoint(_CHECKPOINT_BODY)
    packet = materialize_autonomous_packet(
        "5555", parsed, scoreboard_uri=parsed.scoreboard_uri, revise_cap=5
    )
    assert "cap=5" in packet
    assert "cap=3" not in packet


@pytest.mark.offline
def test_generate_packet_has_no_autonomous_language() -> None:
    """No-regression: the generate packet is NOT mutated into background-lead."""
    parsed = parse_checkpoint(_CHECKPOINT_BODY)
    packet = materialize_resume_packet(
        "5555", parsed, scoreboard_uri=parsed.scoreboard_uri, window_index=1
    )
    assert "background lead" not in packet
    assert "project_ask" not in packet
    assert "sync_restart" not in packet
    assert "exactly one window" in packet  # one-step generate contract intact


@pytest.mark.offline
def test_autonomous_generate_body_matches_default_wire() -> None:
    from scripts.model_manager.ui.controller.charter_runner.executor_defaults import (
        DEFAULT_MODEL,
        DEFAULT_MODEL_KNOBS,
        autonomous_generate_body,
        default_judgment_body,
    )

    body = autonomous_generate_body(
        root_id="5361",
        window_index=2,
        packet_path="universal-llm-gateway/tmp/charter-runner/5361-w2.md",
        subject="autonomous test",
        caller_agent="charter-runner",
    )
    assert body["op"] == "generate"
    assert body["seat"] == "cursor-sdk"
    assert body["model"] == DEFAULT_MODEL == "cursor/grok-4.6"
    assert body["model_knobs"] == DEFAULT_MODEL_KNOBS
    assert "subject" not in body
    assert "tags" not in body
    # Autonomous mandate is packet-side; wire is identical to default generate.
    plain = default_judgment_body(
        root_id="5361",
        window_index=2,
        packet_path="universal-llm-gateway/tmp/charter-runner/5361-w2.md",
        subject="plain",
        caller_agent="charter-runner",
    )
    assert body == plain


@pytest.mark.offline
def test_admission_mode_autonomous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHARTER_ADMISSION_MODE", "autonomous")
    assert tl._admission_mode() == "autonomous"


@pytest.mark.offline
def test_admission_mode_file_overrides_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mode_file = tmp_path / "admission_mode"
    mode_file.write_text("autonomous\n", encoding="utf-8")
    monkeypatch.setattr(tl, "_admission_mode_path", lambda: mode_file)
    monkeypatch.setenv("CHARTER_ADMISSION_MODE", "handoff")
    assert tl._admission_mode() == "autonomous"


@pytest.mark.offline
def test_fire_window_autonomous_posts_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import dispatch_client as dc

    posted: list[tuple[str, dict[str, Any]]] = []

    class _FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"thread_id": "9004", "execution_id": "exec-9004"}

    class _FakeClient:
        async def post(self, path: str, *, json: dict[str, Any]) -> _FakeResp:
            posted.append((path, json))
            return _FakeResp()

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr(dc, "make_async_client", lambda *_a, **_k: _FakeClient())

    async def _run() -> dict[str, Any]:
        return await dc.fire_window(
            "5555",
            "<scope>x</scope>",
            workspace_root=tmp_path,
            window_index=1,
            admission_mode="autonomous",
        )

    result = asyncio.run(_run())
    assert posted and posted[0][0] == "/api/v1/team/dispatch"
    body = posted[0][1]
    assert body["op"] == "generate"
    assert "subject" not in body
    assert "tags" not in body
    assert result["executor"]["model"] == "cursor/grok-4.6"


@pytest.mark.offline
def test_fire_window_handoff_posts_handoff_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import dispatch_client as dc

    posted: list[tuple[str, dict[str, Any]]] = []

    class _FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"thread_id": "9002", "push_reminder": "Open IDE thread 9002"}

    class _FakeClient:
        async def post(self, path: str, *, json: dict[str, Any]) -> _FakeResp:
            posted.append((path, json))
            return _FakeResp()

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr(dc, "make_async_client", lambda *_a, **_k: _FakeClient())

    async def _run() -> dict[str, Any]:
        return await dc.fire_window(
            "5555",
            "<scope>x</scope>",
            workspace_root=tmp_path,
            window_index=1,
            admission_mode="handoff",
        )

    result = asyncio.run(_run())
    assert posted and posted[0][0] == "/api/v1/team/handoff"
    assert posted[0][1]["op"] == "handoff"
    assert posted[0][1]["role"] == "cursor-consult"
    assert result["thread_id"] == "9002"
    assert result["executor"]["role"] == "cursor-consult"
    assert result["executor"]["seat"] == "cursor"


@pytest.mark.offline
def test_fire_window_generate_posts_dispatch_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import dispatch_client as dc

    posted: list[str] = []

    class _FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"thread_id": "9003", "execution_id": "exec-9003"}

    class _FakeClient:
        async def post(self, path: str, *, json: dict[str, Any]) -> _FakeResp:
            posted.append(path)
            return _FakeResp()

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr(dc, "make_async_client", lambda *_a, **_k: _FakeClient())

    async def _run() -> dict[str, Any]:
        return await dc.fire_window(
            "5555",
            "<scope>x</scope>",
            workspace_root=tmp_path,
            window_index=1,
            admission_mode="generate",
        )

    asyncio.run(_run())
    assert posted == ["/api/v1/team/dispatch"]


@pytest.mark.offline
def test_write_handoff_packet_path(tmp_path: Path) -> None:
    from scripts.model_manager.ui.controller.charter_runner.dispatch_client import (
        write_handoff_packet,
    )

    rel = write_handoff_packet(tmp_path, "5555", 2, "<scope>x</scope>")
    assert rel == "tmp/charter-runner/5555-w2.md"
    assert (
        tmp_path / "tmp/charter-runner/5555-w2.md"
    ).read_text() == "<scope>x</scope>"


# ---- tick loop -----------------------------------------------------------


def _healthy_state() -> MagicMock:
    state = MagicMock()
    state.check_cortex_api.return_value = ServiceInfo(
        name="Cortex", status=ServiceStatus.RUNNING
    )
    state.check_agent_bus.return_value = ServiceInfo(
        name="AgentBus", status=ServiceStatus.RUNNING
    )
    return state


@pytest.fixture
def events_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    log: list[tuple[str, dict[str, Any]]] = []

    async def _fake_emit(signal: str, payload: dict[str, Any], **_kw: Any) -> None:
        log.append((signal, payload))

    # Charter emitters bind ``_emit`` at import time in observation_event_charter;
    # patch both the source module and the sibling's closed-over name.
    monkeypatch.setattr("scripts.model_manager.observation_event._emit", _fake_emit)
    monkeypatch.setattr(
        "scripts.model_manager.observation_event_charter._emit", _fake_emit
    )
    monkeypatch.setattr(
        "scripts.model_manager.observation_event_conveyor._emit", _fake_emit
    )
    return log


@pytest.mark.offline
def test_tick_admits_eligible_root(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    fired: list[str] = []
    pointers: list[dict[str, Any]] = []
    # Shared mutable bus state so a posted admission pointer is visible next pass.
    turns_state = _checkpoint_turns()

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return list(turns_state)

    async def fake_pointer(
        root_id: str,
        *,
        window_index: int,
        posted_at_iso: str,
        worker_thread: str = "",
        packet_path: str = "",
        admission_mode: str = "generate",
    ) -> dict:
        pointers.append({"root": root_id, "window": window_index})
        turns_state.append(
            _turn(
                len(turns_state) + 1,
                f"WIP charter-runner window {window_index}",
                f'{{"posted_at": "{posted_at_iso}"}}',
            )
        )
        return {}

    async def fake_fire(
        root_id: str,
        packet_text: str,
        *,
        workspace_root: Path,
        window_index: int = 1,
        subject: str | None = None,
        admission_mode: str = "generate",
        consult_role: str | None = None,
        implement_source_ref: str | None = None,
    ) -> dict:
        fired.append(root_id)
        return {
            "dispatch_id": "w1",
            "thread_id": "w1",
            "push_reminder": "Open thread w1 in Cursor",
        }

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "post_admission_pointer", fake_pointer)
    monkeypatch.setattr(_dc_mod, "fire_window", fake_fire)

    notifies: list[str] = []
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=CapStore(intent_dir=tmp_path / "intent"),
        on_admit=notifies.append,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert fired == ["5555"]
    assert pointers and pointers[0]["window"] == 1
    assert any(sig == "manage.charter.tick.admitted" for sig, _ in events_log)
    assert notifies and "w1" in notifies[0]


@pytest.mark.offline
def test_tick_admits_handoff_mode(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setenv("CHARTER_ADMISSION_MODE", "handoff")
    # Isolate from live ~/.local/share arming file (beats env).
    monkeypatch.setattr(tl, "_admission_mode_path", lambda: tmp_path / "missing")
    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    fired_modes: list[str] = []
    turns_state = _checkpoint_turns()

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return list(turns_state)

    async def fake_pointer(
        root_id: str,
        *,
        window_index: int,
        posted_at_iso: str,
        worker_thread: str = "",
        packet_path: str = "",
        admission_mode: str = "generate",
    ) -> dict:
        turns_state.append(
            _turn(
                len(turns_state) + 1,
                f"WIP charter-runner window {window_index}",
                f'{{"posted_at": "{posted_at_iso}"}}',
            )
        )
        return {}

    async def fake_fire(
        root_id: str,
        packet_text: str,
        *,
        workspace_root: Path,
        window_index: int = 1,
        subject: str | None = None,
        admission_mode: str = "generate",
        consult_role: str | None = None,
        implement_source_ref: str | None = None,
    ) -> dict:
        fired_modes.append(admission_mode)
        assert admission_mode == "handoff"
        assert "from=cursor" in packet_text
        assert "from=cursor-sdk" not in packet_text
        return {
            "dispatch_id": "w-handoff",
            "thread_id": "w-handoff",
            "push_reminder": "Open thread w-handoff in Cursor IDE",
        }

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "post_admission_pointer", fake_pointer)
    monkeypatch.setattr(_dc_mod, "fire_window", fake_fire)

    notifies: list[str] = []
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=CapStore(intent_dir=tmp_path / "intent"),
        on_admit=notifies.append,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert fired_modes == ["handoff"]
    assert any(sig == "manage.charter.tick.admitted" for sig, _ in events_log)
    assert notifies and "attended IDE" in notifies[0]


@pytest.mark.offline
def test_admission_mode_unknown_falls_back_to_generate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Isolate from live ~/.local/share/charter-runner/admission_mode arming file.
    monkeypatch.setattr(tl, "_admission_mode_path", lambda: tmp_path / "missing")
    monkeypatch.setenv("CHARTER_ADMISSION_MODE", "bogus")
    assert tl._admission_mode() == "generate"


@pytest.mark.offline
def test_tick_admits_autonomous_mode(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setenv("CHARTER_ADMISSION_MODE", "autonomous")
    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    fired_modes: list[str] = []
    turns_state = _checkpoint_turns()

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return list(turns_state)

    async def fake_pointer(
        root_id: str,
        *,
        window_index: int,
        posted_at_iso: str,
        worker_thread: str = "",
        packet_path: str = "",
        admission_mode: str = "generate",
    ) -> dict:
        turns_state.append(
            _turn(
                len(turns_state) + 1,
                f"WIP charter-runner window {window_index}",
                f'{{"posted_at": "{posted_at_iso}"}}',
            )
        )
        return {}

    async def fake_fire(
        root_id: str,
        packet_text: str,
        *,
        workspace_root: Path,
        window_index: int = 1,
        subject: str | None = None,
        admission_mode: str = "generate",
        consult_role: str | None = None,
        implement_source_ref: str | None = None,
    ) -> dict:
        fired_modes.append(admission_mode)
        assert admission_mode == "autonomous"
        assert "background lead" in packet_text
        assert "project_ask" in packet_text
        assert subject is not None and "autonomous background lead" in subject
        return {
            "dispatch_id": "w-auto",
            "thread_id": "w-auto",
            "push_reminder": "",
        }

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "post_admission_pointer", fake_pointer)
    monkeypatch.setattr(_dc_mod, "fire_window", fake_fire)

    notifies: list[str] = []
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=CapStore(intent_dir=tmp_path / "intent"),
        on_admit=notifies.append,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert fired_modes == ["autonomous"]
    assert any(sig == "manage.charter.tick.admitted" for sig, _ in events_log)
    assert notifies and "autonomous background lead" in notifies[0]


@pytest.mark.offline
def test_tick_does_not_double_admit_in_flight(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    fired: list[str] = []

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        turns = _checkpoint_turns()
        turns.append(
            _turn(
                3,
                "WIP charter-runner window 1",
                '{"posted_at": "2999-01-01T00:00:00+00:00"}',
            )
        )
        return turns

    async def fake_fire(
        root_id: str,
        packet_text: str,
        *,
        workspace_root: Path,
        window_index: int = 1,
        subject: str | None = None,
        admission_mode: str = "generate",
        consult_role: str | None = None,
        implement_source_ref: str | None = None,
    ) -> dict:
        fired.append(root_id)
        return {}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(_dc_mod, "fire_window", fake_fire)

    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert fired == []  # in-flight guard prevents double admission


@pytest.mark.offline
def test_waiting_open_soft_remind_does_not_fail(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """Attended handoffs wait for operator open — soft remind, never auto-fail."""

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        turns = _checkpoint_turns()
        turns.append(
            _turn(
                3,
                "WIP charter-runner window 1",
                '{"posted_at": "2000-01-01T00:00:00+00:00"}',
            )
        )
        return turns

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)

    caps = CapStore()
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
        unattended_stale_s=0.0,  # hard-fail OFF (attended default)
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert caps.check("5555")[0] is True  # not stopped
    assert any(sig == "manage.charter.tick.waiting_open" for sig, _ in events_log)


@pytest.mark.offline
def test_fire_raise_skips_pointer_and_admitted(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """A2(i)/a:26168: fire_window raises ⇒ no pointer, root stopped, no re-fire."""
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    pointers: list[str] = []
    fired: list[str] = []

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return _checkpoint_turns()

    async def fake_pointer(*_a: Any, **_k: Any) -> dict:
        pointers.append("called")
        return {}

    async def fake_fire(*_a: Any, **_k: Any) -> dict:
        fired.append("fire")
        raise RuntimeError("dispatch 500")

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "post_admission_pointer", fake_pointer)
    monkeypatch.setattr(_dc_mod, "fire_window", fake_fire)

    caps = CapStore(intent_dir=tmp_path / "intent")
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.08)
        await loop.stop()

    asyncio.run(_exercise())
    assert pointers == []
    assert fired == ["fire"]  # exactly one attempt across multiple ticks
    assert not any(sig == "manage.charter.tick.admitted" for sig, _ in events_log)
    allowed, reason = caps.check("5555")
    assert allowed is False
    assert reason == "stopped:admission_exception"
    assert any(
        sig == "manage.charter.tick.window_failed"
        and payload.get("reason") == "admission_exception"
        for sig, payload in events_log
    )


@pytest.mark.offline
def test_fire_503_keeps_intent_stops_root_no_re_admit(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """a:26168: Stargate 503 must not clear intent and thrash-re-admit."""
    import httpx

    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    fired: list[str] = []
    intent_dir = tmp_path / "intent"

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return _checkpoint_turns()

    async def fake_pointer(*_a: Any, **_k: Any) -> dict:
        raise AssertionError("pointer must not post after 503 fire")

    async def fake_fire(*_a: Any, **_k: Any) -> dict:
        fired.append("fire")
        request = httpx.Request("POST", "http://localhost/api/v1/team/dispatch")
        response = httpx.Response(503, request=request, text="unavailable")
        raise httpx.HTTPStatusError(
            "Server error '503'", request=request, response=response
        )

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "post_admission_pointer", fake_pointer)
    monkeypatch.setattr(_dc_mod, "fire_window", fake_fire)

    caps = CapStore(intent_dir=intent_dir)
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.08)
        await loop.stop()

    asyncio.run(_exercise())
    assert fired == ["fire"]
    assert caps.has_admit_intent("5555", 1)
    allowed, reason = caps.check("5555")
    assert allowed is False
    assert reason == "stopped:admission_transport_error"
    assert any(
        sig == "manage.charter.tick.window_failed"
        and payload.get("reason") == "admission_transport_error"
        for sig, payload in events_log
    )


@pytest.mark.offline
def test_pointer_post_fail_no_double_admit(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """A1/A2(ii): fire ok + pointer fail ⇒ no second fire; root stopped."""
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    fired: list[str] = []
    turns_state = _checkpoint_turns()

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return list(turns_state)

    async def fake_pointer(*_a: Any, **_k: Any) -> dict:
        raise RuntimeError("bus post failed")

    async def fake_fire(
        root_id: str,
        packet_text: str,
        *,
        workspace_root: Path,
        window_index: int = 1,
        subject: str | None = None,
        admission_mode: str = "generate",
        consult_role: str | None = None,
        implement_source_ref: str | None = None,
    ) -> dict:
        fired.append(root_id)
        return {
            "dispatch_id": "w1",
            "thread_id": "w1",
            "packet_path": "universal-llm-gateway/tmp/charter-runner/5555-w1.md",
        }

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "post_admission_pointer", fake_pointer)
    monkeypatch.setattr(_dc_mod, "fire_window", fake_fire)

    caps = CapStore(intent_dir=tmp_path / "intent")
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.08)
        await loop.stop()

    asyncio.run(_exercise())
    assert fired == ["5555"]  # exactly one fire across multiple ticks
    assert caps.check("5555")[0] is False
    assert any(
        sig == "manage.charter.tick.window_failed" and p.get("reason") == "pointer_post_failed"
        for sig, p in events_log
    )
    assert not any(sig == "manage.charter.tick.admitted" for sig, _ in events_log)


@pytest.mark.offline
def test_unattended_stale_window_hard_fails(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """A3: when unattended stale threshold is armed, old waiting_open → fail."""

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        turns = _checkpoint_turns()
        turns.append(
            _turn(
                3,
                "WIP charter-runner window 1",
                '{"posted_at": "2000-01-01T00:00:00+00:00"}',
            )
        )
        return turns

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)

    caps = CapStore()
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
        unattended_stale_s=60.0,  # armed; admission is decades old
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert caps.check("5555")[0] is False
    assert any(
        sig == "manage.charter.tick.window_failed" and p.get("reason") == "stale_window"
        for sig, p in events_log
    )


@pytest.mark.offline
def test_autonomous_mode_auto_arms_stale(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """G4: autonomous + env unset → DEFAULT_AUTONOMOUS_STALE_S arms hard-fail."""

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        turns = _checkpoint_turns()
        turns.append(
            _turn(
                3,
                "WIP charter-runner window 1",
                '{"posted_at": "2000-01-01T00:00:00+00:00"}',
            )
        )
        return turns

    monkeypatch.delenv(tl._ENV_UNATTENDED_STALE_S, raising=False)
    monkeypatch.setattr(tl, "_admission_mode", lambda: "autonomous")
    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)

    caps = CapStore()
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
        # override None → resolve via effective (autonomous default)
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert caps.check("5555") == (False, "stopped:stale_window")
    assert any(
        sig == "manage.charter.tick.window_failed" and p.get("reason") == "stale_window"
        for sig, p in events_log
    )


@pytest.mark.offline
def test_handoff_mode_stale_stays_off(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """G4: handoff + env unset → soft waiting_open only; no hard-fail."""

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        turns = _checkpoint_turns()
        turns.append(
            _turn(
                3,
                "WIP charter-runner window 1",
                '{"posted_at": "2000-01-01T00:00:00+00:00"}',
            )
        )
        return turns

    monkeypatch.delenv(tl._ENV_UNATTENDED_STALE_S, raising=False)
    monkeypatch.setattr(tl, "_admission_mode", lambda: "handoff")
    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)

    caps = CapStore()
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert caps.check("5555")[0] is True
    assert any(sig == "manage.charter.tick.waiting_open" for sig, _ in events_log)
    assert not any(
        sig == "manage.charter.tick.window_failed" and p.get("reason") == "stale_window"
        for sig, p in events_log
    )


@pytest.mark.offline
def test_env_zero_forces_off_under_autonomous(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """G4: CHARTER_UNATTENDED_STALE_S=0 under autonomous forces OFF."""

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        turns = _checkpoint_turns()
        turns.append(
            _turn(
                3,
                "WIP charter-runner window 1",
                '{"posted_at": "2000-01-01T00:00:00+00:00"}',
            )
        )
        return turns

    monkeypatch.setenv(tl._ENV_UNATTENDED_STALE_S, "0")
    monkeypatch.setattr(tl, "_admission_mode", lambda: "autonomous")
    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)

    caps = CapStore()
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert caps.check("5555")[0] is True
    assert not any(
        sig == "manage.charter.tick.window_failed" and p.get("reason") == "stale_window"
        for sig, p in events_log
    )


@pytest.mark.offline
def test_stale_already_stopped_no_reemit(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """G4: second tick after stale_window does not duplicate window_failed."""

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        turns = _checkpoint_turns()
        turns.append(
            _turn(
                3,
                "WIP charter-runner window 1",
                '{"posted_at": "2000-01-01T00:00:00+00:00"}',
            )
        )
        return turns

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)

    caps = CapStore()
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
        unattended_stale_s=60.0,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.08)
        await loop.stop()

    asyncio.run(_exercise())
    stale_emits = [
        p
        for sig, p in events_log
        if sig == "manage.charter.tick.window_failed" and p.get("reason") == "stale_window"
    ]
    assert len(stale_emits) == 1


@pytest.mark.offline
def test_already_stopped_other_reason_no_stale_restop(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """A2: stopped:worker_failed + aged admission ⇒ no stale_window restop."""

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        turns = _checkpoint_turns()
        turns.append(
            _turn(
                3,
                "WIP charter-runner window 1",
                '{"posted_at": "2000-01-01T00:00:00+00:00"}',
            )
        )
        return turns

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)

    caps = CapStore()
    caps.mark_failed("5555", "worker_failed")
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
        unattended_stale_s=60.0,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert caps.check("5555") == (False, "stopped:worker_failed")
    assert not any(
        sig == "manage.charter.tick.window_failed" and p.get("reason") == "stale_window"
        for sig, p in events_log
    )


@pytest.mark.offline
def test_terminal_worker_failed_beats_stale(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """A1: aged past default + terminal worker ⇒ worker_failed, zero stale_window."""

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        if root_id == "9001":
            return [_turn(1, "machine closeout", '{"status":"failed"}')]
        return [
            *_checkpoint_turns(),
            _turn(
                3,
                "WIP charter-runner window 1",
                '{"window": 1, "worker_thread": "9001",'
                ' "posted_at": "2000-01-01T00:00:00+00:00"}',
            ),
        ]

    async def fake_thread(thread_id: str) -> dict[str, Any]:
        return {"id": thread_id, "status": "active"}

    monkeypatch.delenv(tl._ENV_UNATTENDED_STALE_S, raising=False)
    monkeypatch.setattr(tl, "_admission_mode", lambda: "autonomous")
    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "fetch_thread", fake_thread)

    caps = CapStore()
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert any(
        sig == "manage.charter.tick.window_failed" and p.get("reason") == "worker_failed"
        for sig, p in events_log
    )
    assert not any(
        sig == "manage.charter.tick.window_failed" and p.get("reason") == "stale_window"
        for sig, p in events_log
    )
    assert caps.check("5555") == (False, "stopped:worker_failed")


@pytest.mark.offline
def test_malformed_env_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """A5: malformed CHARTER_UNATTENDED_STALE_S under autonomous → default arms."""

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        turns = _checkpoint_turns()
        turns.append(
            _turn(
                3,
                "WIP charter-runner window 1",
                '{"posted_at": "2000-01-01T00:00:00+00:00"}',
            )
        )
        return turns

    monkeypatch.setenv(tl._ENV_UNATTENDED_STALE_S, "abc")
    monkeypatch.setattr(tl, "_admission_mode", lambda: "autonomous")
    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)

    caps = CapStore()
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert caps.check("5555") == (False, "stopped:stale_window")


@pytest.mark.offline
def test_negative_env_forces_off(
    monkeypatch: pytest.MonkeyPatch, events_log: list[tuple[str, dict[str, Any]]]
) -> None:
    """A5: negative CHARTER_UNATTENDED_STALE_S under autonomous → force-OFF."""

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        turns = _checkpoint_turns()
        turns.append(
            _turn(
                3,
                "WIP charter-runner window 1",
                '{"posted_at": "2000-01-01T00:00:00+00:00"}',
            )
        )
        return turns

    monkeypatch.setenv(tl._ENV_UNATTENDED_STALE_S, "-1")
    monkeypatch.setattr(tl, "_admission_mode", lambda: "autonomous")
    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)

    caps = CapStore()
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert caps.check("5555")[0] is True
    assert not any(
        sig == "manage.charter.tick.window_failed" and p.get("reason") == "stale_window"
        for sig, p in events_log
    )


@pytest.mark.offline
def test_harvest_emits_closed_event(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """A4: harvested window emits manage.charter.tick.closed with turn + worker."""
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    # Checkpoint with no gated next-pickup so harvest does not re-admit.
    done_body = _CHECKPOINT_BODY.replace(
        "## Next pickup\n1. G2 — implement parser\n2. G3 — wire loop",
        "## Next pickup\nnone",
    )

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        if root_id == "9001":
            return [
                {
                    "turn_number": 1,
                    "from": "cursor-sdk",
                    "subject": "findings",
                    "body": "done",
                }
            ]
        return [
            _turn(1, "WIP 0.1 kickoff"),
            _turn(
                2,
                "WIP charter-runner window 1",
                '{"window": 1, "worker_thread": "9001",'
                ' "posted_at": "2026-07-01T00:00:00+00:00"}',
            ),
            _turn(3, "CHECKPOINT — done", done_body),
        ]

    async def fake_close(worker_thread: str, *, summary: str = "") -> None:
        return None

    async def fake_fire(*_a: Any, **_k: Any) -> dict:
        raise AssertionError("harvest test must not admit a new window")

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "close_worker_thread", fake_close)
    monkeypatch.setattr(_dc_mod, "fire_window", fake_fire)

    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    closed = [p for s, p in events_log if s == "manage.charter.tick.closed"]
    assert closed
    assert closed[0]["worker_thread"] == "9001"
    assert closed[0]["turn_number"] == 3
    assert closed[0]["checkpoint_turn"] == 3


@pytest.mark.offline
def test_manage_charter_tick_audit_registered() -> None:
    """A5: named audit op is discoverable in the operation catalog."""
    from event_store.operation_catalog import get_operation, list_operations
    from event_store.operation_dispatch import _DISPATCH

    op = get_operation("manage.charter.tick.audit")
    assert op is not None
    assert op.name == "manage.charter.tick.audit"
    assert "manage.charter.tick.audit" in {d["name"] for d in list_operations()}
    assert "manage.charter.tick.audit" in _DISPATCH


@pytest.mark.offline
def test_window_log_admit_and_closeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path)
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "harvested")

    path = wl.append_admit(
        root_id="5555",
        window_index=1,
        worker_thread="9001",
        packet_path="universal-llm-gateway/tmp/charter-runner/5555-w1.md",
        packet_text="<scope>dogfood</scope>",
        push_reminder="open IDE",
    )
    assert path == tmp_path / "9001.log"
    assert path.exists()
    text = path.read_text()
    assert "ADMIT" in text and "agent-bus:9001" in text
    assert "<scope>dogfood</scope>" in text
    assert (tmp_path / "root-5555.log").exists()

    wl.append_closeout(
        root_id="5555",
        window_index=1,
        worker_thread="9001",
        checkpoint_subject="CHECKPOINT — done",
        checkpoint_body="## Steps\n1. [x] done",
        worker_turns=[
            {
                "turn_number": 2,
                "from": "cursor",
                "to": "charter-runner",
                "subject": "findings",
                "body": "window complete",
            }
        ],
        worker_closed=True,
    )
    text = path.read_text()
    assert "CLOSEOUT" in text and "window complete" in text
    assert "worker_thread_closed=True" in text
    assert wl.already_harvested("5555", 1)


@pytest.mark.offline
def test_completed_windows_pairs() -> None:
    turns = [
        {"turn_number": 1, "subject": "CHECKPOINT — start", "body": "a"},
        {
            "turn_number": 2,
            "subject": "WIP charter-runner window 1",
            "body": '{"window": 1, "worker_thread": "9"}',
        },
        {"turn_number": 3, "subject": "CHECKPOINT — done", "body": "b"},
    ]
    pairs = tl._completed_windows(turns)
    assert len(pairs) == 1
    assert pairs[0][0]["turn_number"] == 2
    assert pairs[0][1]["turn_number"] == 3


@pytest.mark.offline
def test_unhealthy_services_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    fired: list[str] = []

    async def fake_roots() -> list[dict[str, Any]]:
        raise AssertionError("should not scan when unhealthy")

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)

    state = MagicMock()
    state.check_cortex_api.return_value = ServiceInfo(
        name="Cortex", status=ServiceStatus.UNHEALTHY
    )
    state.check_agent_bus.return_value = ServiceInfo(
        name="AgentBus", status=ServiceStatus.RUNNING
    )

    loop = tl.CharterRunnerTickLoop(
        service_state=state, shutdown_gate=ManageShutdownGate(), tick_interval_s=0.01
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.03)
        await loop.stop()

    asyncio.run(_exercise())
    assert fired == []


# ---- A-R3-1..4 (Opus review amendments) ---------------------------------


@pytest.mark.offline
def test_worker_failed_closeout_stops_root_no_refire(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """A-R3-1: in-flight + worker closeout status=failed ⇒ window_failed, no fire."""
    fired: list[str] = []

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        if root_id == "9001":
            return [
                _turn(1, "machine closeout", '{"status":"failed"}'),
            ]
        return [
            *_checkpoint_turns(),
            _turn(
                3,
                "WIP charter-runner window 1",
                '{"window": 1, "worker_thread": "9001",'
                ' "posted_at": "2999-01-01T00:00:00+00:00"}',
            ),
        ]

    async def fake_fire(*_a: Any, **_k: Any) -> dict:
        fired.append("fired")
        return {}

    async def fake_thread(thread_id: str) -> dict[str, Any]:
        return {"id": thread_id, "status": "active"}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "fetch_thread", fake_thread)
    monkeypatch.setattr(_dc_mod, "fire_window", fake_fire)

    caps = CapStore(intent_dir=tmp_path / "intent")
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert fired == []
    assert caps.check("5555")[0] is False
    assert any(
        sig == "manage.charter.tick.window_failed"
        and p.get("reason") == "worker_failed"
        for sig, p in events_log
    )


@pytest.mark.offline
def test_t57_empty_sentinel_halts_gated_pickup() -> None:
    """A-R3-2: real G6-DONE Next-pickup empty-sentinel ⇒ no gated pickup / no admit."""
    body = _CHECKPOINT_BODY.replace(
        "## Next pickup\n1. G2 — implement parser\n2. G3 — wire loop",
        "## Next pickup\n"
        "_(empty — gated lane complete; empty ≠ arc complete for tangentials T1–T3)_",
    )
    parsed = parse_checkpoint(body)
    assert parsed.next_pickup_gated is False
    decision = evaluate_root("5361", [_turn(57, "CHECKPOINT — G6 DONE", body)], CapStore())
    assert decision.eligible is False
    assert decision.reason == "no_gated_pickup"


@pytest.mark.offline
def test_harvest_idempotent_across_restart(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """A-R3-3: wipe ephemeral /tmp logs; durable harvest marker ⇒ no re-close/closed."""
    import shutil

    from scripts.model_manager.ui.controller.charter_runner import window_log as wl
    from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
        emit_footer,
    )

    ephemeral = tmp_path / "cr"
    durable = tmp_path / "durable-harvested"
    monkeypatch.setattr(wl, "LOG_DIR", ephemeral)
    monkeypatch.setattr(wl, "_HARVESTED_DIR", durable)

    footer = emit_footer(
        schema_version=1,
        status="CHECKPOINT",
        next_pickup={"gid": "pending", "lane": "judgment", "executor": "pending"},
        wip=None,
        consult={"role": None, "poll_hint": None, "from": None},
        revise_count=0,
        evidence=[],
        window_id="charter-5555-w1",
        transition_id=None,
    )
    done_body = _CHECKPOINT_BODY.replace(
        "## Next pickup\n1. G2 — implement parser\n2. G3 — wire loop",
        "## Next pickup\nnone",
    ) + f"\n\n{footer}\n"
    close_calls: list[str] = []

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        if root_id == "9001":
            return [{"turn_number": 1, "from": "cursor-sdk", "subject": "x", "body": "y"}]
        return [
            _turn(1, "WIP 0.1 kickoff"),
            _turn(
                2,
                "WIP charter-runner window 1",
                '{"window": 1, "worker_thread": "9001",'
                ' "posted_at": "2026-07-01T00:00:00+00:00"}',
            ),
            _turn(3, "CHECKPOINT — done", done_body),
        ]

    async def fake_close(worker_thread: str, *, summary: str = "") -> None:
        close_calls.append(worker_thread)

    async def fake_fire(*_a: Any, **_k: Any) -> dict:
        raise AssertionError("harvest test must not admit")

    async def fake_fetch_thread(thread_id: str) -> dict[str, Any]:
        return {
            "id": thread_id,
            "status": "active",
            "tags": ["charter-runner"],
            "summary": "",
        }

    async def fake_close_root(root_id: str, *, summary: str = "") -> dict[str, Any]:
        return {"id": root_id, "status": "closed"}

    async def fake_unenroll(root_id: str) -> dict[str, Any]:
        return {"id": root_id, "tags": [], "unenrolled": True}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "close_worker_thread", fake_close)
    monkeypatch.setattr(tl.bus_client, "fetch_thread", fake_fetch_thread)
    monkeypatch.setattr(tl.bus_client, "close_root_thread", fake_close_root)
    monkeypatch.setattr(tl.bus_client, "unenroll_root", fake_unenroll)
    monkeypatch.setattr(_dc_mod, "fire_window", fake_fire)

    async def _one_pass(caps: CapStore) -> None:
        loop = tl.CharterRunnerTickLoop(
            service_state=_healthy_state(),
            shutdown_gate=ManageShutdownGate(),
            workspace_root=Path("/tmp/workspace"),
            tick_interval_s=0.01,
            caps=caps,
        )
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    # Pass 1: harvest emits closed + closes worker; durable marker written.
    asyncio.run(_one_pass(CapStore(intent_dir=tmp_path / "intent1")))
    assert close_calls == ["9001"]
    closed_pass1 = [p for s, p in events_log if s == "manage.charter.tick.closed"]
    assert len(closed_pass1) == 1
    assert wl.already_harvested("5555", 1)

    # Simulate manage restart wiping ephemeral /tmp logs (not durable markers).
    if ephemeral.exists():
        shutil.rmtree(ephemeral)
    assert not ephemeral.exists()
    assert wl.already_harvested("5555", 1)  # durable marker survives

    close_calls.clear()
    events_log.clear()

    # Pass 2: reconstructed loop must not re-close or re-emit closed.
    asyncio.run(_one_pass(CapStore(intent_dir=tmp_path / "intent2")))
    assert close_calls == []
    assert [p for s, p in events_log if s == "manage.charter.tick.closed"] == []


@pytest.mark.offline
def test_admit_intent_blocks_refire_after_crash_before_pointer(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """A-R3-4: fire ok + crash before pointer ⇒ reconstructed loop does not re-fire."""
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    intent_dir = tmp_path / "intent"
    fired: list[str] = []
    crash_once = {"armed": True}

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return _checkpoint_turns()

    async def fake_pointer(*_a: Any, **_k: Any) -> dict:
        if crash_once["armed"]:
            crash_once["armed"] = False
            raise RuntimeError("simulated crash before pointer-post")
        raise AssertionError("pointer must not be reached on reconstructed loop")

    async def fake_fire(
        root_id: str,
        packet_text: str,
        *,
        workspace_root: Path,
        window_index: int = 1,
        subject: str | None = None,
        admission_mode: str = "generate",
        consult_role: str | None = None,
        implement_source_ref: str | None = None,
    ) -> dict:
        fired.append(root_id)
        return {
            "dispatch_id": "w1",
            "thread_id": "w1",
            "packet_path": "universal-llm-gateway/tmp/charter-runner/5555-w1.md",
        }

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "post_admission_pointer", fake_pointer)
    monkeypatch.setattr(_dc_mod, "fire_window", fake_fire)

    async def _one_pass(caps: CapStore) -> None:
        loop = tl.CharterRunnerTickLoop(
            service_state=_healthy_state(),
            shutdown_gate=ManageShutdownGate(),
            workspace_root=Path("/tmp/workspace"),
            tick_interval_s=0.01,
            caps=caps,
        )
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    # Pass 1: fire succeeds, pointer raises (crash gap); intent left on disk.
    caps1 = CapStore(intent_dir=intent_dir)
    asyncio.run(_one_pass(caps1))
    assert fired == ["5555"]
    assert caps1.has_admit_intent("5555", 1)

    # Pass 2: fresh CapStore sharing durable intent dir — must not re-fire or heal.
    fired.clear()
    events_log.clear()
    caps2 = CapStore(intent_dir=intent_dir)
    caps2.bind_intent_worker("5555", 1, "w1")

    async def live_worker_failure(_thread: str) -> None:
        return None

    async def live_worker_fetch(_thread: str) -> dict:
        return {"status": "active"}

    monkeypatch.setattr(
        tl.bus_client, "worker_failure_reason", live_worker_failure
    )
    monkeypatch.setattr(tl.bus_client, "fetch_thread", live_worker_fetch)
    asyncio.run(_one_pass(caps2))
    assert fired == []
    assert caps2.has_admit_intent("5555", 1)
    assert any(
        sig == "manage.charter.tick.root_skipped"
        and p.get("reason") == "admit_intent_orphan"
        for sig, p in events_log
    )
    assert not any(
        sig == "manage.charter.tick.intent_healed" for sig, _ in events_log
    )


@pytest.mark.offline
def test_orphan_intent_healed_via_tick_when_worker_absent(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """Orphan intent without WIP/worker clears via heal — next window can admit."""
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")
    intent_dir = tmp_path / "intent"
    fired: list[str] = []

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return _checkpoint_turns()

    async def fake_fire(
        root_id: str,
        packet_text: str,
        *,
        workspace_root: Path,
        window_index: int = 1,
        subject: str | None = None,
        admission_mode: str = "generate",
        consult_role: str | None = None,
        implement_source_ref: str | None = None,
    ) -> dict:
        fired.append(root_id)
        return {
            "dispatch_id": "w2",
            "thread_id": "w2",
            "packet_path": "universal-llm-gateway/tmp/charter-runner/5555-w1.md",
        }

    async def fake_pointer(*_a: Any, **_k: Any) -> dict:
        return {"ok": True}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "post_admission_pointer", fake_pointer)
    monkeypatch.setattr(_dc_mod, "fire_window", fake_fire)

    caps = CapStore(intent_dir=intent_dir)
    caps.mark_admit_intent("5555", 1)

    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
    )

    async def _run() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_run())
    assert fired == ["5555"]
    heal_idx = next(
        i for i, (sig, _) in enumerate(events_log) if sig == "manage.charter.tick.intent_healed"
    )
    admit_idx = next(
        i for i, (sig, _) in enumerate(events_log) if sig == "manage.charter.tick.admitted"
    )
    assert heal_idx < admit_idx
    assert events_log[heal_idx][1]["root"] == "5555"
    assert events_log[heal_idx][1]["window_index"] == 1


# ---- R-amendment modules (F1/F2/F3) -------------------------------------


@pytest.mark.offline
def test_r_verdict_gate_fail_closed() -> None:
    from scripts.model_manager.charter_control.r_verdict_gate import (
        RGateAction,
        advance_allowed,
        parse_r_verdict,
    )

    assert advance_allowed("Merits verdict: ADMIT") is True
    assert advance_allowed("Merits: RATIFY — scope ok") is True
    assert parse_r_verdict("Merits: ADMIT_WITH_AMENDMENTS").action is (
        RGateAction.AMENDMENTS_REQUIRED
    )
    assert parse_r_verdict("Merits verdict: RETURN").action is RGateAction.BLOCKED
    assert parse_r_verdict("Merits: SCOPE-DRIFT").action is RGateAction.BLOCKED
    assert parse_r_verdict("no verdict here").action is RGateAction.BLOCKED


@pytest.mark.offline
def test_revise_cap_blocks_admission(tmp_path: Path) -> None:
    caps = CapStore(intent_dir=tmp_path / "intent", revise_dir=tmp_path / "revise", revise_cap=3)
    body = _CHECKPOINT_BODY.replace(
        "## Next pickup\n1. G2 — implement parser\n2. G3 — wire loop",
        "## Next pickup\n1. G4a — revise after probe fail",
    )
    caps._revise_dir.mkdir(parents=True, exist_ok=True)
    caps.revise_path("5555").write_text("3\n", encoding="utf-8")
    decision = evaluate_root("5555", [_turn(2, "CHECKPOINT revise cap", body)], caps)
    assert decision.eligible is False
    assert decision.reason == "revise_cap_exhausted"


@pytest.mark.offline
def test_revise_counter_increment(tmp_path: Path) -> None:
    caps = CapStore(intent_dir=tmp_path / "intent", revise_dir=tmp_path / "revise")
    assert caps.get_revise_count("5555") == 0
    assert caps.increment_revise("5555") == 1
    assert caps.get_revise_count("5555") == 1
    caps.reset_revise("5555")
    assert caps.get_revise_count("5555") == 0


# ---- Phase A/B consult hooks (charter-window-consult-hooks) -------------


_CONSULT_CHECKPOINT_BODY = """\
# CHECKPOINT — CONSULT_PENDING

## Steps
1. [x] Q complete
2. [ ] Implement after consult

## In-flight / WIP
none

## Next pickup
1. CONSULT_PENDING — judgment gap on G4 implement bind

## Frictions
_None this window._

## Sidecars
- cortex://notes/system/specs/foo.md — pinned corpus
- agent-bus:8801 — consult thread (pending)

Scoreboard: cortex://notes/system/threads/5555-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""


@pytest.mark.offline
def test_parse_consult_pending() -> None:
    parsed = parse_checkpoint(_CONSULT_CHECKPOINT_BODY)
    assert parsed.consult_pending is True
    assert parsed.wip_is_none is True


@pytest.mark.offline
def test_negated_consult_token_is_not_pending() -> None:
    """A worker's ``¬ re-CONSULT_PENDING`` disclaimer must not re-flag pending.

    Regression for friction 25984: the negated token in a resumed CHECKPOINT's
    Next-pickup drove 10+ stale consult admissions on agent-bus:5632.
    """
    body = _CONSULT_CHECKPOINT_BODY.replace(
        "1. CONSULT_PENDING — judgment gap on G4 implement bind",
        "1. Fold R amendments into dense spec, then G4 implement\n"
        "2. ¬ re-CONSULT_PENDING; ¬ re-fire R; ¬ nested cursor-sdk consult",
    )
    parsed = parse_checkpoint(body)
    assert parsed.consult_pending is False
    # Plain English negation (bind L1-A regress a:25984).
    plain = _CONSULT_CHECKPOINT_BODY.replace(
        "1. CONSULT_PENDING — judgment gap on G4 implement bind",
        "1. G4a — continue; do not re-CONSULT_PENDING",
    )
    assert parse_checkpoint(plain).consult_pending is False
    # Active directive shapes must still fire.
    assert parse_checkpoint(_CONSULT_CHECKPOINT_BODY).consult_pending is True
    stop_body = body + "\n## Stop\nCONSULT_PENDING\n"
    assert parse_checkpoint(stop_body).consult_pending is True
    # Canonical Stop: line (bind regress).
    stop_line = _CHECKPOINT_BODY.replace(
        "## Next pickup\n1. G2 — implement parser",
        "## Next pickup\n1. Stop: CONSULT_PENDING + consult_role: r_admit",
    )
    assert parse_checkpoint(stop_line).consult_pending is True


@pytest.mark.offline
def test_consult_pending_eligibility_admits_consult_not_worker() -> None:
    turns = [_turn(2, "CHECKPOINT CONSULT_PENDING", _CONSULT_CHECKPOINT_BODY)]
    decision = evaluate_root("5555", turns, CapStore())
    assert decision.eligible is True
    assert decision.reason == "eligible_consult"
    assert decision.window_kind == "consult"


@pytest.mark.offline
def test_consult_pending_without_gated_pickup_still_eligible() -> None:
    body = _CONSULT_CHECKPOINT_BODY.replace(
        "## Next pickup\n1. CONSULT_PENDING — judgment gap on G4 implement bind",
        "## Next pickup\n1. finish without gated id\n\n## Stop\nCONSULT_PENDING",
    )
    parsed = parse_checkpoint(body)
    assert parsed.consult_pending is True
    assert parsed.next_pickup_gated is False
    decision = evaluate_root("5555", [_turn(2, "CHECKPOINT", body)], CapStore())
    assert decision.eligible is True
    assert decision.window_kind == "consult"


@pytest.mark.offline
def test_autonomous_packet_encodes_consult_boundary() -> None:
    from scripts.model_manager.ui.controller.charter_runner.window_exec import (
        materialize_autonomous_packet,
    )

    parsed = parse_checkpoint(_CHECKPOINT_BODY)
    packet = materialize_autonomous_packet(
        "5555", parsed, scoreboard_uri=parsed.scoreboard_uri, window_index=1
    )
    assert "CONSULT_PENDING" in packet
    assert "depth-1" in packet
    assert "nested" in packet.lower() and "team_dispatch" in packet


@pytest.mark.offline
def test_consult_packet_materializer() -> None:
    from scripts.model_manager.ui.controller.charter_runner.window_exec import (
        materialize_consult_packet,
    )

    parsed = parse_checkpoint(_CONSULT_CHECKPOINT_BODY)
    packet = materialize_consult_packet(
        "5555", parsed, scoreboard_uri=parsed.scoreboard_uri, window_index=2
    )
    assert "CONSULT_PENDING" in packet
    assert "depth-1" in packet
    assert "must NOT dispatch" in packet or "must NOT use team_dispatch" in packet


@pytest.mark.offline
def test_r_verdict_same_family_rejects_advance() -> None:
    from scripts.model_manager.charter_control.r_verdict_gate import (
        RGateAction,
        advance_allowed_with_independence,
        parse_r_verdict_with_independence,
    )

    body = "Merits verdict: ADMIT"
    assert advance_allowed_with_independence(
        body, r_family="cursor", implement_family="anthropic"
    )
    assert not advance_allowed_with_independence(
        body, r_family="cursor", implement_family="cursor"
    )
    parsed = parse_r_verdict_with_independence(
        body, r_family="grok", implement_family="grok"
    )
    assert parsed.action is RGateAction.BLOCKED
    assert parsed.reason == "same_family_r_pre_check_only"


@pytest.mark.offline
def test_terminal_discipline_blocks_done_without_proof() -> None:
    from scripts.model_manager.ui.controller.charter_runner.terminal_discipline import (
        gated_step_done_allowed,
        has_resolvable_terminal,
    )

    blocked = gated_step_done_allowed(step_status="done", terminal_evidence="no uri here")
    assert blocked.ok is False
    assert blocked.code == "terminal_missing"
    ok = gated_step_done_allowed(
        step_status="done",
        terminal_evidence="proof: cortex://notes/system/specs/foo.md",
    )
    assert ok.ok is True
    err_ok = has_resolvable_terminal("", terminal_error="probe_timeout")
    assert err_ok.ok is True
    assert err_ok.code == "probe_timeout"


@pytest.mark.offline
def test_tick_admits_consult_pending(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setenv("CHARTER_ADMISSION_MODE", "autonomous")
    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    fired_modes: list[str] = []
    turns_state = [_turn(2, "CHECKPOINT CONSULT", _CONSULT_CHECKPOINT_BODY)]

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return list(turns_state)

    async def fake_pointer(
        root_id: str,
        *,
        window_index: int,
        posted_at_iso: str,
        worker_thread: str = "",
        packet_path: str = "",
        admission_mode: str = "generate",
    ) -> dict:
        turns_state.append(
            _turn(
                3,
                f"WIP charter-runner window {window_index}",
                f'{{"posted_at": "{posted_at_iso}"}}',
            )
        )
        return {}

    async def fake_fire(
        root_id: str,
        packet_text: str,
        *,
        workspace_root: Path,
        window_index: int = 1,
        subject: str | None = None,
        admission_mode: str = "generate",
        consult_role: str | None = None,
        implement_source_ref: str | None = None,
    ) -> dict:
        fired_modes.append(admission_mode)
        assert admission_mode == "consult"
        assert consult_role == "judgment_gap"
        assert "CONSULT_PENDING" in packet_text
        assert subject is not None and "consult window" in subject
        return {"dispatch_id": "w-consult", "thread_id": "w-consult", "push_reminder": ""}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "post_admission_pointer", fake_pointer)
    monkeypatch.setattr(_dc_mod, "fire_window", fake_fire)

    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=CapStore(intent_dir=tmp_path / "intent"),
    )

    async def _exercise() -> None:
        await loop.start()
        await asyncio.sleep(0.05)
        await loop.stop()

    asyncio.run(_exercise())
    assert fired_modes == ["consult"]
    assert any(sig == "manage.charter.tick.admitted" for sig, _ in events_log)


@pytest.mark.offline
def test_consult_boundary_dogfood_fixture_trace() -> None:
    """Fixture trace for AC7 — live dogfood evidence pending G7 close."""
    from implement_admission.dense_spec_schema import dense_spec_hash_uri
    from implement_admission.implement_ready import evaluate_implement_ready

    from scripts.model_manager.ui.controller.charter_runner.admission import (
        evaluate_root,
    )

    spec_text = _VALID_DENSE_SPEC_FOR_DOGFOOD
    spec_uri = "notes/system/specs/charter-window-consult-hooks.md"
    spec_hash = dense_spec_hash_uri(spec_text)

    consult_cp = _CONSULT_CHECKPOINT_BODY
    consult_decision = evaluate_root(
        "5539", [_turn(10, "CHECKPOINT CONSULT", consult_cp)], CapStore()
    )
    assert consult_decision.window_kind == "consult"

    resume_body = consult_cp.replace(
        "## Next pickup\n1. CONSULT_PENDING — judgment gap on G4 implement bind",
        "## Next pickup\n1. G4 — implement after consult",
    ) + (
        "\n## Consult provenance\n"
        "- consult_thread: agent-bus:8801\n"
        "- verdict: proceed_with_amendments\n"
        "- consultant_family: anthropic\n"
        "- consultant_substrate: web-anthropic\n"
        "- evidence: cortex://notes/system/threads/8801-consult-reply.md\n"
    )
    resume_parsed = parse_checkpoint(resume_body)
    assert resume_parsed.consult_pending is False
    worker_decision = evaluate_root(
        "5539", [_turn(11, "CHECKPOINT resume", resume_body)], CapStore()
    )
    assert worker_decision.window_kind == "worker"
    assert worker_decision.eligible is True

    verdict = evaluate_implement_ready(
        todo_id="todo:charter-window-consult-hooks",
        density_triage="judgment_required",
        source_uri=spec_uri,
        implement_ready_assertion_id=1,
        assertion={
            "entity_id": "todo:charter-window-consult-hooks",
            "superseded_by": None,
            "valid_until": None,
            "evidence_uris": [spec_uri, spec_hash],
        },
        now_iso="2026-07-22T00:00:00+00:00",
        dense_spec_uri=spec_uri,
        dense_spec_text=spec_text,
        files_expected=["scripts/model_manager/ui/controller/charter_runner/eligibility.py"],
        acceptance_criteria=["Consult hooks land."],
        consult_thread="agent-bus:8801",
        verdict="proceed_with_amendments",
        consultant_family="anthropic",
        consultant_substrate="web-anthropic",
        skeptic_ratified=True,
    )
    assert verdict.admitted is True


_VALID_DENSE_SPEC_FOR_DOGFOOD = """\
# Dense test spec

## 1. Problem

A problem exists.

## 2. Non-goals / scope exclusions

Out of scope items.

## 3. Source-of-truth / provenance

| Source | Role |
|---|---|
| spec | authoritative |

## 4. Touch-point inventory

- module.py

## 5. Bound design decisions / fork table

| Fork | Decision |
|---|---|
| 1 | resolved |

## 6. Implementation guidance

Build consult hooks.

## 7. Acceptance criteria

1. Consult hooks land.

## 8. Verification / quality gates

- pytest green

<reasoning_trace>

No fork remains OPEN.

</reasoning_trace>
"""


# ---- G3 silent-starve exits (skip telemetry + state-close) -----------------


def _no_gated_pickup_turns() -> list[dict[str, Any]]:
    body = _CHECKPOINT_BODY.replace("1. G2 — implement parser", "1. finish the thing")
    body = body.replace("2. G3 — wire loop", "2. also this")
    return [_turn(2, "CHECKPOINT wave 2", body)]


@pytest.mark.offline
def test_tick_no_gated_pickup_state_close_then_unenroll(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl
    from scripts.model_manager.ui.controller.charter_runner.admission import (
        ENROLLMENT_TAG,
    )

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    closes: list[str] = []
    unenrolls: list[str] = []
    tags_state = {ENROLLMENT_TAG, "other"}

    async def fake_roots() -> list[dict[str, Any]]:
        if ENROLLMENT_TAG not in tags_state:
            return []
        return [{"id": "5705", "tags": list(tags_state)}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return _no_gated_pickup_turns()

    async def fake_close(root_id: str, *, summary: str) -> dict[str, Any]:
        closes.append(root_id)
        assert "no_gated_pickup" in summary
        assert "_state_close" not in summary
        return {"status": "closed"}

    async def fake_unenroll(root_id: str) -> dict[str, Any]:
        unenrolls.append(root_id)
        tags_state.discard(ENROLLMENT_TAG)
        return {"tags": list(tags_state), "unenrolled": True}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "close_root_thread", fake_close)
    monkeypatch.setattr(tl.bus_client, "unenroll_root", fake_unenroll)

    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=CapStore(intent_dir=tmp_path / "intent"),
    )

    async def _exercise() -> None:
        await loop._tick_once()

    asyncio.run(_exercise())
    assert closes == ["5705"]
    assert unenrolls == ["5705"]
    # close before unenroll
    assert closes and unenrolls
    skipped = [p for s, p in events_log if s == "manage.charter.tick.root_skipped"]
    closed = [p for s, p in events_log if s == "manage.charter.tick.root_closed"]
    scanned = [p for s, p in events_log if s == "manage.charter.tick.scanned"]
    assert skipped and skipped[0]["reason"] == "no_gated_pickup"
    assert closed and closed[0]["closed"] is True and closed[0]["unenrolled"] is True
    assert closed[0]["checkpoint_turn"] == 2
    assert scanned and scanned[0]["skipped_by_reason"]["no_gated_pickup"] == 1


@pytest.mark.offline
def test_closeout_next_pickup_tick_non_regression(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """A2: Next-pickup ``G6 — R-after`` must not state-close / unenroll."""
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl
    from scripts.model_manager.ui.controller.charter_runner.admission import (
        ENROLLMENT_TAG,
    )

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    closes: list[str] = []
    unenrolls: list[str] = []
    turns_state = _checkpoint_with_next_pickup("G6 — R-after")

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5712", "tags": [ENROLLMENT_TAG]}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return list(turns_state)

    async def fake_close(root_id: str, *, summary: str) -> dict[str, Any]:
        closes.append(root_id)
        return {"status": "closed"}

    async def fake_unenroll(root_id: str) -> dict[str, Any]:
        unenrolls.append(root_id)
        return {"tags": [], "unenrolled": True}

    async def fake_pointer(
        root_id: str,
        *,
        window_index: int,
        posted_at_iso: str,
        worker_thread: str = "",
        packet_path: str = "",
        admission_mode: str = "generate",
    ) -> dict:
        turns_state.append(
            _turn(
                len(turns_state) + 1,
                f"WIP charter-runner window {window_index}",
                f'{{"posted_at": "{posted_at_iso}"}}',
            )
        )
        return {}

    async def fake_fire(
        root_id: str,
        packet_text: str,
        *,
        workspace_root: Path,
        window_index: int = 1,
        subject: str | None = None,
        admission_mode: str = "generate",
        consult_role: str | None = None,
        implement_source_ref: str | None = None,
    ) -> dict:
        return {
            "dispatch_id": "w-g6",
            "thread_id": "w-g6",
            "push_reminder": "Open thread w-g6",
        }

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "close_root_thread", fake_close)
    monkeypatch.setattr(tl.bus_client, "unenroll_root", fake_unenroll)
    monkeypatch.setattr(tl.bus_client, "post_admission_pointer", fake_pointer)
    monkeypatch.setattr(_dc_mod, "fire_window", fake_fire)

    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=CapStore(intent_dir=tmp_path / "intent"),
    )

    async def _exercise() -> None:
        await loop._tick_once()

    asyncio.run(_exercise())
    assert closes == []
    assert unenrolls == []
    skipped = [p for s, p in events_log if s == "manage.charter.tick.root_skipped"]
    closed = [p for s, p in events_log if s == "manage.charter.tick.root_closed"]
    assert not any(p.get("reason") == "no_gated_pickup" for p in skipped)
    assert closed == []


@pytest.mark.offline
def test_tick_close_raises_keeps_tag_emits_closed_false(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    unenrolls: list[str] = []

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5705"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return _no_gated_pickup_turns()

    async def fake_close(root_id: str, *, summary: str) -> dict[str, Any]:
        raise RuntimeError("close PATCH failed")

    async def fake_unenroll(root_id: str) -> dict[str, Any]:
        unenrolls.append(root_id)
        return {"tags": ["charter-runner"], "unenrolled": False}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    async def fake_fetch_thread(root_id: str) -> dict[str, Any]:
        # A5 already-closed probe hits the live bus otherwise, which makes this
        # test depend on whether root 5705 happens to be closed right now.
        return {"status": "active"}

    monkeypatch.setattr(tl.bus_client, "close_root_thread", fake_close)
    monkeypatch.setattr(tl.bus_client, "unenroll_root", fake_unenroll)
    monkeypatch.setattr(tl.bus_client, "fetch_thread", fake_fetch_thread)

    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=CapStore(intent_dir=tmp_path / "intent"),
    )
    asyncio.run(loop._tick_once())
    assert unenrolls == []  # A3: no unenroll on close failure
    closed = [p for s, p in events_log if s == "manage.charter.tick.root_closed"]
    assert closed and closed[0]["closed"] is False
    assert closed[0]["unenrolled"] is False


@pytest.mark.offline
def test_tick_max_state_closes_per_tick_bounds_blast(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    closes: list[str] = []

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5705"}, {"id": "5706"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return _no_gated_pickup_turns()

    async def fake_close(root_id: str, *, summary: str) -> dict[str, Any]:
        closes.append(root_id)
        return {"status": "closed"}

    async def fake_unenroll(root_id: str) -> dict[str, Any]:
        return {"tags": [], "unenrolled": True}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "close_root_thread", fake_close)
    monkeypatch.setattr(tl.bus_client, "unenroll_root", fake_unenroll)

    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=CapStore(intent_dir=tmp_path / "intent"),
    )
    asyncio.run(loop._tick_once())
    assert closes == ["5705"]  # A4: only one state-close
    skipped = [p for s, p in events_log if s == "manage.charter.tick.root_skipped"]
    assert len(skipped) == 2  # both emit skip
    closed = [p for s, p in events_log if s == "manage.charter.tick.root_closed"]
    assert len(closed) == 1


@pytest.mark.offline
def test_tick_operator_fork_emits_skip_no_close(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    closes: list[str] = []
    body = _CHECKPOINT_BODY.replace(
        "1. G2 — implement parser", "1. Operator: decide schema shape"
    )

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return [_turn(2, "CHECKPOINT wave 2", body)]

    async def fake_close(root_id: str, *, summary: str) -> dict[str, Any]:
        closes.append(root_id)
        return {}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "close_root_thread", fake_close)

    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=CapStore(intent_dir=tmp_path / "intent"),
    )
    asyncio.run(loop._tick_once())
    assert closes == []
    skipped = [p for s, p in events_log if s == "manage.charter.tick.root_skipped"]
    assert skipped and skipped[0]["reason"] == "operator_fork"
    assert not any(s == "manage.charter.tick.root_closed" for s, _ in events_log)


@pytest.mark.offline
def test_enrollment_tag_absent_predicate() -> None:
    from scripts.model_manager.ui.controller.charter_runner.state_close import (
        enrollment_tag_absent,
    )

    assert enrollment_tag_absent({"tags": [], "unenrolled": True}) is True
    assert enrollment_tag_absent({"tags": ["charter-runner"]}) is False
    assert enrollment_tag_absent({}) is False  # not bool(dict)
    assert enrollment_tag_absent(["other"]) is True


# ---- G5 stale R-corpus sha (F5 / a:26095) ---------------------------------


def _r_admit_checkpoint_body(*, sidecar_row: str) -> str:
    return f"""\
# CHECKPOINT — CONSULT_PENDING r_admit

## Steps
1. [x] densify
2. [ ] R-admit

## In-flight / WIP
none

## Next pickup
1. CONSULT_PENDING — G3 R-admit · consult_role: r_admit

## Frictions
_None this window._

## Sidecars
{sidecar_row}

Scoreboard: cortex://notes/system/threads/5555-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline body → do not read linearly.
"""


@pytest.mark.offline
def test_r_corpus_sha_ok_when_pin_matches_file(tmp_path: Path) -> None:
    from scripts.model_manager.ui.controller.charter_runner.r_corpus_sha import (
        file_sha256_hex,
        verify_r_corpus_sha,
    )

    spec = tmp_path / "notes/system/specs/foo.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# dense\n", encoding="utf-8")
    hex_digest = file_sha256_hex(spec)
    body = _r_admit_checkpoint_body(
        sidecar_row=(
            f"- Dense spec: cortex://notes/system/specs/foo.md · "
            f"spec_sha256:{hex_digest}"
        )
    )
    result = verify_r_corpus_sha(body, cortex_root=tmp_path)
    assert result.ok is True
    assert result.pinned_hex == hex_digest
    assert result.live_hex == hex_digest


@pytest.mark.offline
def test_r_corpus_sha_stale_when_pin_superseded(tmp_path: Path) -> None:
    from scripts.model_manager.ui.controller.charter_runner.r_corpus_sha import (
        verify_r_corpus_sha,
    )

    spec = tmp_path / "notes/system/specs/foo.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# live\n", encoding="utf-8")
    stale = "a" * 64
    body = _r_admit_checkpoint_body(
        sidecar_row=(
            f"- Dense spec: cortex://notes/system/specs/foo.md · "
            f"spec_sha256:{stale}"
        )
    )
    result = verify_r_corpus_sha(body, cortex_root=tmp_path)
    assert result.ok is False
    assert result.reason == "stale_r_corpus_sha"
    assert result.sub_reason == "stale"


@pytest.mark.offline
def test_r_corpus_sha_missing_pin() -> None:
    from scripts.model_manager.ui.controller.charter_runner.r_corpus_sha import (
        verify_r_corpus_sha,
    )

    body = _r_admit_checkpoint_body(
        sidecar_row="- cortex://notes/system/specs/foo.md — dense"
    )
    result = verify_r_corpus_sha(body, cortex_root=Path("/tmp"))
    assert result.ok is False
    assert result.sub_reason == "missing_pin"


@pytest.mark.offline
def test_r_corpus_sha_missing_uri() -> None:
    from scripts.model_manager.ui.controller.charter_runner.r_corpus_sha import (
        verify_r_corpus_sha,
    )

    body = _r_admit_checkpoint_body(sidecar_row=f"- pin only · spec_sha256:{'b' * 64}")
    result = verify_r_corpus_sha(body, cortex_root=Path("/tmp"))
    assert result.ok is False
    assert result.sub_reason == "missing_uri"


@pytest.mark.offline
def test_r_corpus_sha_ambiguous_pin(tmp_path: Path) -> None:
    from scripts.model_manager.ui.controller.charter_runner.r_corpus_sha import (
        file_sha256_hex,
        verify_r_corpus_sha,
    )

    for name in ("foo.md", "bar.md"):
        p = tmp_path / "notes/system/specs" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(name, encoding="utf-8")
    h1 = file_sha256_hex(tmp_path / "notes/system/specs/foo.md")
    h2 = file_sha256_hex(tmp_path / "notes/system/specs/bar.md")
    body = _r_admit_checkpoint_body(
        sidecar_row=(
            f"- Dense spec: cortex://notes/system/specs/foo.md · spec_sha256:{h1}\n"
            f"- Dense spec: cortex://notes/system/specs/bar.md · spec_sha256:{h2}"
        )
    )
    result = verify_r_corpus_sha(body, cortex_root=tmp_path)
    assert result.ok is False
    assert result.sub_reason == "ambiguous_pin"


@pytest.mark.offline
def test_r_corpus_sha_malformed_pin() -> None:
    from scripts.model_manager.ui.controller.charter_runner.r_corpus_sha import (
        verify_r_corpus_sha,
    )

    body = _r_admit_checkpoint_body(
        sidecar_row=(
            "- Dense spec: cortex://notes/system/specs/foo.md · "
            "spec_sha256:deadbeef"
        )
    )
    result = verify_r_corpus_sha(body, cortex_root=Path("/tmp"))
    assert result.ok is False
    assert result.sub_reason == "malformed_pin"


@pytest.mark.offline
def test_r_corpus_sha_unreadable(tmp_path: Path) -> None:
    from scripts.model_manager.ui.controller.charter_runner.r_corpus_sha import (
        verify_r_corpus_sha,
    )

    body = _r_admit_checkpoint_body(
        sidecar_row=(
            f"- Dense spec: cortex://notes/system/specs/missing.md · "
            f"spec_sha256:{'c' * 64}"
        )
    )
    result = verify_r_corpus_sha(body, cortex_root=tmp_path)
    assert result.ok is False
    assert result.sub_reason == "unreadable"


@pytest.mark.offline
def test_r_corpus_sha_strips_markdown_backticks_around_uri(tmp_path: Path) -> None:
    """Backticked cortex URIs must not keep the trailing ` (unreadable path)."""
    from scripts.model_manager.ui.controller.charter_runner.r_corpus_sha import (
        extract_r_corpus_pin,
        file_sha256_hex,
        verify_r_corpus_sha,
    )

    spec = tmp_path / "notes" / "system" / "specs" / "friction-26332.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# dense\n", encoding="utf-8")
    digest = file_sha256_hex(spec)
    body = _r_admit_checkpoint_body(
        sidecar_row=(
            f"- Dense spec: `cortex://notes/system/specs/friction-26332.md` · "
            f"`spec_sha256:{digest}`"
        )
    )
    pin = extract_r_corpus_pin(body)
    assert pin.ok is True
    assert pin.dense_spec_uri == "cortex://notes/system/specs/friction-26332.md"
    assert "`" not in (pin.dense_spec_uri or "")
    assert verify_r_corpus_sha(body, cortex_root=tmp_path).ok is True


@pytest.mark.offline
def test_r_corpus_sha_trailing_punct_not_unreadable(tmp_path: Path) -> None:
    """Glued trailing `` ` . ) `` must not classify as missing-file unreadable."""
    from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
        materialize_checkpoint_turn,
        normalize_checkpoint_machine_fields,
    )
    from scripts.model_manager.ui.controller.charter_runner.r_corpus_sha import (
        file_sha256_hex,
        verify_r_corpus_sha,
    )

    spec = tmp_path / "notes" / "system" / "specs" / "friction-26332.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# dense\n", encoding="utf-8")
    digest = file_sha256_hex(spec)
    raw = _r_admit_checkpoint_body(
        sidecar_row=(
            f"- Dense spec: `cortex://notes/system/specs/friction-26332.md`). · "
            f"spec_sha256:{digest}"
        )
    )
    canonical = normalize_checkpoint_machine_fields(raw)
    assert "`cortex://" not in canonical
    assert "friction-26332.md)." not in canonical
    assert verify_r_corpus_sha(raw, cortex_root=tmp_path).ok is True
    materialized = materialize_checkpoint_turn(
        {"turn_number": 1, "subject": "CHECKPOINT", "body": raw}
    )
    assert materialized["body"] == canonical
    assert verify_r_corpus_sha(materialized["body"], cortex_root=tmp_path).ok is True


@pytest.mark.offline
def test_r_corpus_sha_malformed_uri_vs_unreadable(tmp_path: Path) -> None:
    from scripts.model_manager.ui.controller.charter_runner.r_corpus_sha import (
        verify_r_corpus_sha,
    )

    missing = _r_admit_checkpoint_body(
        sidecar_row=(
            f"- Dense: cortex://notes/system/specs/missing.md · "
            f"spec_sha256:{'c' * 64}"
        )
    )
    assert verify_r_corpus_sha(missing, cortex_root=tmp_path).sub_reason == "unreadable"

    escape = _r_admit_checkpoint_body(
        sidecar_row=(
            f"- Dense: cortex://notes/system/specs/../../../../etc/passwd · "
            f"spec_sha256:{'d' * 64}"
        )
    )
    assert verify_r_corpus_sha(escape, cortex_root=tmp_path).sub_reason == "malformed_uri"


@pytest.mark.offline
def test_evaluate_root_materialize_normalizes_sidecars_before_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admit path sees canonical URI after materialize (no backtick residue)."""
    from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
    from scripts.model_manager.ui.controller.charter_runner.admission import (
        evaluate_root,
    )
    from scripts.model_manager.ui.controller.charter_runner.r_corpus_sha import (
        file_sha256_hex,
        verify_r_corpus_sha,
    )

    spec = tmp_path / "notes" / "system" / "specs" / "friction-26332.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# dense\n", encoding="utf-8")
    digest = file_sha256_hex(spec)
    body = _r_admit_checkpoint_body(
        sidecar_row=(
            f"- Dense: `cortex://notes/system/specs/friction-26332.md` · "
            f"`spec_sha256:{digest}`"
        )
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel._admission_mode",
        lambda: "autonomous",
    )
    decision = evaluate_root(
        "5555",
        [{"turn_number": 1, "subject": "CHECKPOINT wave 1", "body": body}],
        CapStore(intent_dir=tmp_path / "intent"),
    )
    assert decision.checkpoint is not None
    cp_body = str(decision.checkpoint.get("body") or "")
    assert "`cortex://" not in cp_body
    assert verify_r_corpus_sha(cp_body, cortex_root=tmp_path).ok is True


@pytest.mark.offline
def test_r_corpus_sha_hex_equals_read_sha256(tmp_path: Path) -> None:
    """A5: helper hex equals hashlib of same bytes (fs read_sha256 equivalence)."""
    import hashlib

    from scripts.model_manager.ui.controller.charter_runner.r_corpus_sha import (
        file_sha256_hex,
    )

    path = tmp_path / "notes/system/specs/eq.md"
    path.parent.mkdir(parents=True)
    payload = b"dense-spec-bytes-for-equivalence\n"
    path.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert file_sha256_hex(path) == expected


@pytest.mark.offline
def test_materializer_emits_stale_r_corpus_sha_marker() -> None:
    from scripts.model_manager.ui.controller.charter_runner.window_exec import (
        materialize_autonomous_packet,
    )
    from scripts.model_manager.ui.controller.charter_runner.window_exec import (
        materialize_consult_packet,
    )

    auto = materialize_autonomous_packet(
        "5555",
        parse_checkpoint(_CHECKPOINT_BODY),
        scoreboard_uri="cortex://notes/system/threads/5555-charter-scoreboard.md",
        window_index=1,
    )
    assert "[stale-r-corpus-sha]" in auto

    r_body = _r_admit_checkpoint_body(
        sidecar_row=(
            f"- Dense spec: cortex://notes/system/specs/foo.md · "
            f"spec_sha256:{'d' * 64}"
        )
    )
    consult = materialize_consult_packet(
        "5555",
        parse_checkpoint(r_body),
        scoreboard_uri="cortex://notes/system/threads/5555-charter-scoreboard.md",
        window_index=2,
    )
    assert "[stale-r-corpus-sha]" in consult


def _r_admit_tick_harness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    body: str,
    cortex_root: Path,
) -> tuple[list[dict[str, Any]], CapStore, list[tuple[str, Any]]]:
    from scripts.model_manager.ui.controller.charter_runner import r_corpus_sha as rcs
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")
    monkeypatch.setattr(rcs, "cortex_files_root", lambda: cortex_root)
    monkeypatch.setattr(rcs, "_refusal_store_dir", lambda: tmp_path / "r-refusals")
    rcs.clear_r_corpus_refusals("5555")

    fired: list[dict[str, Any]] = []
    turns_state = [_turn(2, "CHECKPOINT CONSULT r_admit", body)]
    intent_dir = tmp_path / "intent"
    caps = CapStore(intent_dir=intent_dir)

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5555"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return list(turns_state)

    async def fake_pointer(
        root_id: str,
        *,
        window_index: int,
        posted_at_iso: str,
        worker_thread: str = "",
        packet_path: str = "",
        admission_mode: str = "generate",
        consult_role: str | None = None,
        implement_source_ref: str | None = None,
    ) -> dict:
        turns_state.append(
            _turn(
                3,
                f"WIP charter-runner window {window_index}",
                f'{{"posted_at": "{posted_at_iso}"}}',
            )
        )
        return {}

    async def fake_fire(
        root_id: str,
        packet_text: str,
        *,
        workspace_root: Path,
        window_index: int = 1,
        subject: str | None = None,
        admission_mode: str = "generate",
        consult_role: str | None = None,
        implement_source_ref: str | None = None,
    ) -> dict:
        fired.append(
            {
                "admission_mode": admission_mode,
                "consult_role": consult_role,
                "subject": subject,
            }
        )
        return {"dispatch_id": "w-r", "thread_id": "w-r", "push_reminder": ""}

    async def fake_post_checkpoint(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "post_admission_pointer", fake_pointer)
    monkeypatch.setattr(tl.bus_client, "post_root_checkpoint", fake_post_checkpoint)
    monkeypatch.setattr(_dc_mod, "fire_window", fake_fire)
    return fired, caps, turns_state


@pytest.mark.offline
def test_admit_window_skips_fire_on_stale_r_corpus(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    cortex = tmp_path / "cortex"
    spec = cortex / "notes/system/specs/foo.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# live\n", encoding="utf-8")
    body = _r_admit_checkpoint_body(
        sidecar_row=(
            f"- Dense spec: cortex://notes/system/specs/foo.md · "
            f"spec_sha256:{'e' * 64}"
        )
    )
    fired, caps, _ = _r_admit_tick_harness(
        monkeypatch, tmp_path, body=body, cortex_root=cortex
    )
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
    )
    asyncio.run(loop._tick_once())
    assert fired == []
    assert not any(caps.intent_path("5555", 1).exists() for _ in [0])
    assert not list(caps._intent_dir.glob("*.intent"))
    skipped = [p for s, p in events_log if s == "manage.charter.tick.root_skipped"]
    assert skipped and skipped[0]["reason"].startswith("stale_r_corpus_sha")


@pytest.mark.offline
def test_admit_window_fires_on_matching_pin(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """A4 positive control: matching pin ⇒ fire_window called."""
    from scripts.model_manager.ui.controller.charter_runner.r_corpus_sha import (
        file_sha256_hex,
    )

    cortex = tmp_path / "cortex"
    spec = cortex / "notes/system/specs/foo.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# match\n", encoding="utf-8")
    hex_digest = file_sha256_hex(spec)
    body = _r_admit_checkpoint_body(
        sidecar_row=(
            f"- Dense spec: cortex://notes/system/specs/foo.md · "
            f"spec_sha256:{hex_digest}"
        )
    )
    fired, caps, _ = _r_admit_tick_harness(
        monkeypatch, tmp_path, body=body, cortex_root=cortex
    )
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
    )
    asyncio.run(loop._tick_once())
    assert len(fired) == 1
    assert fired[0]["admission_mode"] == "consult"
    assert fired[0]["consult_role"] == "r_admit"
    assert any(s == "manage.charter.tick.admitted" for s, _ in events_log)


# ---- G3 state-close-on-stale (A6 / a:26093; R-admit A1–A9) -----------------


def _aged_in_flight_turns() -> list[dict[str, Any]]:
    turns = _checkpoint_turns()
    turns.append(
        _turn(
            3,
            "WIP charter-runner window 1",
            '{"posted_at": "2000-01-01T00:00:00+00:00"}',
        )
    )
    return turns


@pytest.mark.offline
def test_stale_window_state_close_then_unenroll(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """Aged autonomous admission → stale stop + close + unenroll."""
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl
    from scripts.model_manager.ui.controller.charter_runner.admission import (
        ENROLLMENT_TAG,
    )

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    closes: list[str] = []
    unenrolls: list[str] = []
    tags_state = {ENROLLMENT_TAG, "other"}

    async def fake_roots() -> list[dict[str, Any]]:
        if ENROLLMENT_TAG not in tags_state:
            return []
        return [{"id": "5801", "tags": list(tags_state)}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return _aged_in_flight_turns()

    async def fake_close(root_id: str, *, summary: str) -> dict[str, Any]:
        closes.append(root_id)
        assert "stale_window" in summary
        return {"status": "closed"}

    async def fake_unenroll(root_id: str) -> dict[str, Any]:
        unenrolls.append(root_id)
        tags_state.discard(ENROLLMENT_TAG)
        return {"tags": list(tags_state), "unenrolled": True}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "close_root_thread", fake_close)
    monkeypatch.setattr(tl.bus_client, "unenroll_root", fake_unenroll)

    caps = CapStore(intent_dir=tmp_path / "intent")
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
        unattended_stale_s=60.0,
    )
    asyncio.run(loop._tick_once())
    assert closes == ["5801"]
    assert unenrolls == ["5801"]
    assert caps.check("5801") == (False, "stopped:stale_window")
    closed = [p for s, p in events_log if s == "manage.charter.tick.root_closed"]
    assert len(closed) == 1
    assert closed[0]["reason"] == "stale_window"
    assert closed[0]["closed"] is True and closed[0]["unenrolled"] is True
    assert any(
        s == "manage.charter.tick.window_failed" and p.get("reason") == "stale_window"
        for s, p in events_log
    )


@pytest.mark.offline
def test_stale_window_state_close_respects_a4(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """Two stale roots in one tick ⇒ ≤1 close; second recovers via skip path."""
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl
    from scripts.model_manager.ui.controller.charter_runner.admission import (
        ENROLLMENT_TAG,
    )

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    closes: list[str] = []
    enrolled = {"5802", "5803"}

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": rid, "tags": [ENROLLMENT_TAG]} for rid in sorted(enrolled)]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return _aged_in_flight_turns()

    async def fake_close(root_id: str, *, summary: str) -> dict[str, Any]:
        closes.append(root_id)
        return {"status": "closed"}

    async def fake_unenroll(root_id: str) -> dict[str, Any]:
        enrolled.discard(root_id)
        return {"tags": [], "unenrolled": True}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "close_root_thread", fake_close)
    monkeypatch.setattr(tl.bus_client, "unenroll_root", fake_unenroll)

    caps = CapStore(intent_dir=tmp_path / "intent")
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
        unattended_stale_s=60.0,
    )
    asyncio.run(loop._tick_once())
    assert len(closes) == 1  # A4
    assert caps.check("5802") == (False, "stopped:stale_window")
    assert caps.check("5803") == (False, "stopped:stale_window")
    # Second tick: skip-path recovery for the deferred root (exact match).
    asyncio.run(loop._tick_once())
    assert sorted(closes) == ["5802", "5803"]
    closed = [p for s, p in events_log if s == "manage.charter.tick.root_closed"]
    # A3: exactly one root_closed per root lifetime.
    assert len(closed) == 2
    roots_closed = sorted(p["root"] for p in closed)
    assert roots_closed == ["5802", "5803"]
    assert all(p["reason"] == "stale_window" for p in closed)


@pytest.mark.offline
def test_a4_mixed_no_gated_pickup_and_stale_same_tick(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """One no_gated_pickup + one stale in same tick ⇒ ≤1 close total (A4)."""
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl
    from scripts.model_manager.ui.controller.charter_runner.admission import (
        ENROLLMENT_TAG,
    )

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    closes: list[str] = []

    async def fake_roots() -> list[dict[str, Any]]:
        return [
            {"id": "5810", "tags": [ENROLLMENT_TAG]},
            {"id": "5811", "tags": [ENROLLMENT_TAG]},
        ]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        if root_id == "5810":
            return _no_gated_pickup_turns()
        return _aged_in_flight_turns()

    async def fake_close(root_id: str, *, summary: str) -> dict[str, Any]:
        closes.append(root_id)
        return {"status": "closed"}

    async def fake_unenroll(root_id: str) -> dict[str, Any]:
        return {"tags": [], "unenrolled": True}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "close_root_thread", fake_close)
    monkeypatch.setattr(tl.bus_client, "unenroll_root", fake_unenroll)

    caps = CapStore(intent_dir=tmp_path / "intent")
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
        unattended_stale_s=60.0,
    )
    asyncio.run(loop._tick_once())
    assert len(closes) == 1
    closed = [p for s, p in events_log if s == "manage.charter.tick.root_closed"]
    assert len(closed) == 1


@pytest.mark.offline
def test_window_in_flight_without_stale_stop_no_state_close(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """In-flight below stale threshold ⇒ no state-close (A1)."""
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    closes: list[str] = []

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5820"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        turns = _checkpoint_turns()
        # Fresh admission — below remind and stale thresholds.
        turns.append(
            _turn(
                3,
                "WIP charter-runner window 1",
                '{"posted_at": "2099-01-01T00:00:00+00:00"}',
            )
        )
        return turns

    async def fake_close(root_id: str, *, summary: str) -> dict[str, Any]:
        closes.append(root_id)
        return {"status": "closed"}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "close_root_thread", fake_close)

    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=CapStore(intent_dir=tmp_path / "intent"),
        unattended_stale_s=60.0,
    )
    asyncio.run(loop._tick_once())
    assert closes == []
    assert not any(s == "manage.charter.tick.root_closed" for s, _ in events_log)


@pytest.mark.offline
def test_stopped_worker_failed_aged_admission_no_stale_close(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """stopped:worker_failed + aged admission ⇒ no stale stop and no state-close (A2)."""
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    closes: list[str] = []

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "5830"}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return _aged_in_flight_turns()

    async def fake_close(root_id: str, *, summary: str) -> dict[str, Any]:
        closes.append(root_id)
        return {"status": "closed"}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "close_root_thread", fake_close)

    caps = CapStore(intent_dir=tmp_path / "intent")
    caps.mark_failed("5830", "worker_failed")
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
        unattended_stale_s=60.0,
    )
    asyncio.run(loop._tick_once())
    assert closes == []
    assert caps.check("5830") == (False, "stopped:worker_failed")
    assert not any(
        s == "manage.charter.tick.window_failed" and p.get("reason") == "stale_window"
        for s, p in events_log
    )
    assert not any(s == "manage.charter.tick.root_closed" for s, _ in events_log)


@pytest.mark.offline
def test_stale_close_unenroll_retry_after_partial(
    monkeypatch: pytest.MonkeyPatch,
    events_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    """Close ok / unenroll fail then later tick retries unenroll (A5)."""
    from scripts.model_manager.ui.controller.charter_runner import window_log as wl
    from scripts.model_manager.ui.controller.charter_runner.admission import (
        ENROLLMENT_TAG,
    )

    monkeypatch.setattr(wl, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(wl, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")

    close_calls = 0
    unenroll_calls = 0
    tags_state = {ENROLLMENT_TAG}
    thread_status = {"status": "active"}

    async def fake_roots() -> list[dict[str, Any]]:
        if ENROLLMENT_TAG not in tags_state:
            return []
        return [{"id": "5840", "tags": list(tags_state)}]

    async def fake_turns(root_id: str) -> list[dict[str, Any]]:
        return _aged_in_flight_turns()

    async def fake_close(root_id: str, *, summary: str) -> dict[str, Any]:
        nonlocal close_calls
        close_calls += 1
        if close_calls == 1:
            thread_status["status"] = "closed"
            return {"status": "closed"}
        # Second tick: already closed — raise so A5 already-closed probe runs.
        raise RuntimeError("already closed")

    async def fake_fetch_thread(root_id: str) -> dict[str, Any]:
        return {"id": root_id, "status": thread_status["status"], "tags": list(tags_state)}

    async def fake_unenroll(root_id: str) -> dict[str, Any]:
        nonlocal unenroll_calls
        unenroll_calls += 1
        if unenroll_calls == 1:
            raise RuntimeError("unenroll PATCH failed")
        tags_state.discard(ENROLLMENT_TAG)
        return {"tags": list(tags_state), "unenrolled": True}

    monkeypatch.setattr(tl.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tl.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tl.bus_client, "close_root_thread", fake_close)
    monkeypatch.setattr(tl.bus_client, "fetch_thread", fake_fetch_thread)
    monkeypatch.setattr(tl.bus_client, "unenroll_root", fake_unenroll)

    caps = CapStore(intent_dir=tmp_path / "intent")
    loop = tl.CharterRunnerTickLoop(
        service_state=_healthy_state(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=Path("/tmp/workspace"),
        tick_interval_s=0.01,
        caps=caps,
        unattended_stale_s=60.0,
    )
    asyncio.run(loop._tick_once())
    assert close_calls == 1
    assert unenroll_calls == 1
    closed1 = [p for s, p in events_log if s == "manage.charter.tick.root_closed"]
    assert closed1 and closed1[0]["closed"] is True and closed1[0]["unenrolled"] is False
    # Still enrolled → second tick: already-closed close = success, unenroll retries.
    asyncio.run(loop._tick_once())
    assert close_calls == 2
    assert unenroll_calls == 2
    assert ENROLLMENT_TAG not in tags_state
    closed_all = [p for s, p in events_log if s == "manage.charter.tick.root_closed"]
    assert closed_all[-1]["closed"] is True and closed_all[-1]["unenrolled"] is True


@pytest.mark.offline
def test_repair_checkpoint_footer_null_next_pickup() -> None:
    from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
        repair_checkpoint_footer_body,
        validate_checkpoint_footer,
    )
    from scripts.model_manager.ui.controller.charter_runner.harvest_footer_gate import (
        footer_field_path,
        prepared_checkpoint_body,
    )

    body = """TYPE: CHECKPOINT

## Next-pickup

```charter-state
{
  "schema_version": 1,
  "status": "CHECKPOINT",
  "next_pickup": {"gid": null, "lane": null, "executor": null},
  "wip": null,
  "consult": {"role": null, "poll_hint": null, "from": null},
  "revise_count": 0,
  "evidence": [],
  "window_id": "charter-6518-w1",
  "transition_id": null
}
```
"""
    assert validate_checkpoint_footer(body).ok is False
    repaired, changed = repair_checkpoint_footer_body(body)
    assert changed is True
    assert validate_checkpoint_footer(repaired).ok
    ok_after, _ = footer_field_path(body)
    assert ok_after is True
    assert '"gid": "none"' in prepared_checkpoint_body(body)
