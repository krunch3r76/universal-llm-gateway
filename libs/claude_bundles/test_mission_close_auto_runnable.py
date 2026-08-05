"""Unit tests for the auto-runnable residual gate.

Bullets are quoted from the agent-bus:6655 ep27 MISSION_CLOSEOUT, which passed
the wake-token gate while leaving an mcp restart and a plugin install waiting on
the human operator.
"""

from __future__ import annotations

import pytest

from claude_bundles.mission_close_auto_runnable import (
    check_auto_runnable_items,
    is_auto_runnable,
)
from claude_bundles.mission_close_wake import (
    BEYOND_HEADING,
    refusal_envelope,
    validate_mission_close_wake,
)

pytestmark = pytest.mark.offline

# Verbatim ep27 bullets.
_EP27_MCP_RESTART = (
    "**mcp restart — deliberately deferred, not forgotten.** Restarting mcp "
    "from inside this stream drops this seat's own connector, so the correct "
    "order is restart → wait healthy → continuity hop. · collector: "
    "cursor-auto · followup: contract:propagate mcp, then wait_healthy(mcp), "
    "then open the next CDP operator window on THIS lane (6655) with a "
    "handoff — in that order"
)
_EP27_PLUGIN_INSTALL = (
    "**Cursor ecosystem plugin install** — `scripts/cursor/"
    "install-ecosystem-plugin.sh` for the guard + guidance to reach the IDE "
    "surface. · collector: cursor-auto"
)
_EP27_RELOAD_WINDOW = (
    "**Reload Window** — the one thing no commissioned seat can do. · "
    "operator_gate: IDE restart is the human's hand (inv 24's single exception)"
)


def _closeout(*items: str) -> str:
    bullets = "".join(f"- {item}\n" for item in items)
    return f"TYPE: MISSION_CLOSEOUT\n\n{BEYOND_HEADING}\n{bullets}"


def test_ep27_mcp_restart_refused_without_commission_ref() -> None:
    verdict = validate_mission_close_wake(body=_closeout(_EP27_MCP_RESTART))
    assert verdict.ok is False
    assert verdict.reason == "mission_close_uncommissioned_auto_runnable"
    assert "mcp restart" in verdict.missed_tokens[0]
    assert "deferred:" in verdict.fix_hint


def test_ep27_plugin_install_refused_without_commission_ref() -> None:
    verdict = validate_mission_close_wake(body=_closeout(_EP27_PLUGIN_INSTALL))
    assert verdict.ok is False
    assert verdict.reason == "mission_close_uncommissioned_auto_runnable"
    env = refusal_envelope(verdict)
    assert env["status"] == "blocked"
    assert "collector:` names who" in env["fix_hint"]


def test_bare_reload_window_operator_gate_stays_legal() -> None:
    """Invariant 24's single standing human exception is not auto-runnable."""
    assert is_auto_runnable(_EP27_RELOAD_WINDOW) is False
    assert validate_mission_close_wake(body=_closeout(_EP27_RELOAD_WINDOW)).ok is True


def test_plugin_install_parked_on_operator_refused() -> None:
    item = (
        "plugin install so the guard reaches the IDE — operator_gate: needs "
        "his hand at the keyboard"
    )
    verdict = validate_mission_close_wake(body=_closeout(item))
    assert verdict.ok is False
    assert verdict.reason == "mission_close_operator_gate_for_auto_runnable"
    assert "invariant 24" in verdict.fix_hint


@pytest.mark.parametrize(
    "ref",
    [
        "commissioned 6655#1521",
        "fired on thread 6655",
        "see t1521",
        "dispatch auto-1ef1ad8eacf4",
        "restart_intent_id: f616776d",
    ],
)
def test_commission_reference_forms_pass(ref: str) -> None:
    item = f"mcp restart — collector: cursor-auto · followup: {ref}"
    assert validate_mission_close_wake(body=_closeout(item)).ok is True


def test_lane_number_alone_is_not_a_commission_reference() -> None:
    """Naming the lane you would post to is not proof that you posted."""
    item = (
        "mcp sync_restart — collector: cursor-auto · followup: open the next "
        "window on THIS lane (6655)"
    )
    verdict = validate_mission_close_wake(body=_closeout(item))
    assert verdict.ok is False
    assert verdict.reason == "mission_close_uncommissioned_auto_runnable"


def test_named_deferral_beside_wake_token_passes() -> None:
    item = (
        "Stargate stale on closeout_models.py — folds into the next stargate "
        "propagate · followup: next stargate propagate · deferred: optional "
        "pydantic field only, no capability depends on it"
    )
    assert validate_mission_close_wake(body=_closeout(item)).ok is True


def test_deferral_without_wake_token_still_refused() -> None:
    """`deferred:` excuses the missing commission, never the wake path."""
    item = "mcp sync_restart · deferred: connector churn not worth it tonight"
    verdict = validate_mission_close_wake(body=_closeout(item))
    assert verdict.ok is False
    assert verdict.reason == "mission_close_wake_path_incomplete"


def test_charter_enrollment_passes() -> None:
    item = "plugin sync for the new skill — charter_enrolled: 6655"
    assert validate_mission_close_wake(body=_closeout(item)).ok is True


def test_non_auto_runnable_item_unaffected() -> None:
    item = (
        "D10 B-iii thin spec — collector: web-anthropic · followup: poll "
        "agent-bus:6576 after status:done"
    )
    assert is_auto_runnable(item) is False
    assert validate_mission_close_wake(body=_closeout(item)).ok is True


def test_ep27_residual_leg_with_turn_passes() -> None:
    """Row 16's docstring leg was genuinely commissioned; it must not refuse."""
    item = (
        "docstring/comment honesty for isolation_materialized — collector: "
        "cursor-auto · followup: commissioned at t1503, executor-sized S"
    )
    assert validate_mission_close_wake(body=_closeout(item)).ok is True


def test_first_refusal_reported_when_several_offend() -> None:
    verdict = validate_mission_close_wake(
        body=_closeout(_EP27_MCP_RESTART, _EP27_PLUGIN_INSTALL, _EP27_RELOAD_WINDOW),
    )
    assert verdict.ok is False
    assert len(verdict.missed_tokens) == 1
    assert "mcp restart" in verdict.missed_tokens[0]


def test_check_returns_none_for_clean_items() -> None:
    assert check_auto_runnable_items([]) is None
    assert check_auto_runnable_items(["something — collector: cursor-auto"]) is None
