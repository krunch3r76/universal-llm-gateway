"""Offline tests for operator-proxy mission prompt ensure."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from unittest import mock

import pytest

from claude_bundles.act_receipt import parse_act_receipt
from claude_bundles.operator_proxy_hop_status import HOP_STATUS_MARKER
from claude_bundles.operator_proxy_mission import (
    _BRIEFING_BLOCK,
    _FORBIDDEN_HEADING,
    LIFE_SURFACE_FORBIDDEN_TOOLS,
    LIFE_SURFACE_LEGAL_TOOLS,
    MISSION_SKILL_SLUGS,
    ensure_operator_proxy_mission_prompt,
    is_operator_proxy_mission_purpose,
    purpose_implies_mission,
)
from claude_bundles.runbook_excerpt import extract_sections

pytestmark = pytest.mark.offline


def test_purpose_recognition() -> None:
    assert is_operator_proxy_mission_purpose("operator-proxy")
    assert is_operator_proxy_mission_purpose("mission")
    assert is_operator_proxy_mission_purpose("OPERATOR_PROXY")
    assert not is_operator_proxy_mission_purpose("ask")
    assert purpose_implies_mission("ask", "purpose: mission\n# Body")
    assert not purpose_implies_mission("ask", "# Sealed R-admit")


def test_ensure_injects_briefing_without_slash_prefix() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\nDo the thing.\n")
    assert not out.startswith("/cdp-operator-proxy")
    assert "`cdp-operator-proxy`" in out
    assert "`agent-bus-discipline`" in out
    assert "Status / rank / liveness register (BINDING — member 6)" in out
    assert "## This hop (read first)" in out
    assert out.index("## This hop (read first)") < out.index(
        "## Mission seat map (BINDING"
    )
    assert "## Mission seat map (BINDING" in out
    assert "cursor-auto-tick-work-posting.md" in out
    assert "# Mission\nDo the thing." in out
    assert "## Life surface act path (BINDING)" in out
    assert "## ACT-RECEIPT (BINDING" in out


def test_ensure_injects_self_scheduled_wake_guide() -> None:
    """First-dispatch briefing suspends keep-alive; sole-wake / one-off CDP OK."""
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    assert "cdp-seat-wake-heartbeat.md" in out
    assert "Self-scheduled wake" in out or "keep-alive" in out.lower()
    assert "SUSPENDED" in out
    assert "Do not arm Monitor" in out
    assert "send_later" in out
    assert "TaskStop" in out
    assert "Sole-wake" in out or "sole-wake" in out.lower() or "PRIMARY" in out
    # Historical arm recipes must not ship as first-dispatch defaults.
    # §8 may *name* the forbidden tokens as a warning; they must not be arm instructions.
    assert "Do not arm Monitor" in out
    assert "does NOT make Monitor unbounded" in out
    assert "while true; do sleep 240" not in out
    assert "Consume-time wake affinity" in out
    assert "Absence is not permission" in out
    assert "missing (file absent under a visible root): default STAND_DOWN" in out


def test_ensure_idempotent_and_strips_legacy_slash_prefix() -> None:
    once = ensure_operator_proxy_mission_prompt("TYPE: DIRECTIVE\nintent: birth\n")
    twice = ensure_operator_proxy_mission_prompt(once)
    assert not twice.startswith("/cdp-operator-proxy")
    assert twice.count("## Mission seat map (BINDING") == 1
    assert twice.count("## This hop (read first)") == 1
    assert twice.count("/ulg-for-llms") == 0
    assert twice.rstrip("\n") == once.rstrip("\n")
    legacy = ensure_operator_proxy_mission_prompt(
        "/cdp-operator-proxy\n/reasoning-posture\n\n# Already chipped\n"
    )
    assert not legacy.startswith("/cdp-operator-proxy")
    assert "# Already chipped" in legacy
    assert "## Mission seat map (BINDING" in legacy


def test_structural_briefing_commission_is_ulg_code_team_dispatch() -> None:
    """Seat map and act path name ulg-code team_dispatch; forbidden line does not."""
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    act = out.split("## Life surface act path", 1)[1]
    act_head = act.split(_FORBIDDEN_HEADING, 1)[0]
    assert "team_dispatch" in act_head
    assert "ulg-code" in act_head
    assert "lane=B" in act_head or "`lane=B`" in act_head
    forbidden = out.split(_FORBIDDEN_HEADING, 1)[1]
    forbidden_body = forbidden.split("\n## ", 1)[0]
    for name in ("team_dispatch", "manage", "observability"):
        assert f"`{name}`" not in forbidden_body
    assert "`panel_dispatch`" in forbidden_body
    assert "`claudeburst`" in forbidden_body
    assert "lane-act-gates" in MISSION_SKILL_SLUGS


def test_mission_skill_slugs_include_lane_act_gates() -> None:
    assert "lane-act-gates" in MISSION_SKILL_SLUGS


def test_legal_subset_forbidden_disjoint_a9() -> None:
    assert LIFE_SURFACE_LEGAL_TOOLS.isdisjoint(LIFE_SURFACE_FORBIDDEN_TOOLS)
    for tool in LIFE_SURFACE_LEGAL_TOOLS:
        assert f"`{tool}`" in ensure_operator_proxy_mission_prompt("# x\n")


def test_operator_proxy_mission_seat_map_names_reachable_independent_check() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    seat_section = out.split("## Life surface act path")[0]
    assert "cdp/fable" in seat_section
    assert "cursor/grok-4.7" in seat_section
    assert "cursor/composer-2.5" in seat_section
    assert "cursor/claude-opus-5" not in seat_section
    assert "charter-runner" not in seat_section
    assert "cursor/gpt-5.6-terra" not in seat_section
    _legacy_reasoner = "".join(("cursor/", "gr", "ok", "-4.6"))
    assert _legacy_reasoner not in seat_section


def test_briefing_one_operator_cse_per_lane() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    assert "One operator CSE per lane" in out
    assert "predecessors, not peers" in out
    assert "Never touch operator CSEs on other lanes" in out


def test_briefing_receipt_example_parses_d3() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    marker = "```act-receipt"
    start = out.index(marker)
    end = out.index("```", start + len(marker))
    fence = out[start : end + 3]
    parsed = parse_act_receipt(fence)
    assert parsed is not None
    assert parsed.commission_kind == "team_dispatch"


def test_operator_restart_is_manage_sync_restart_not_propagate() -> None:
    """Kaywan 2026-09-29 ~12:55Z: this seat restarts with manage sync_restart."""
    from claude_bundles.operator_proxy_tier_m import tier_m_authoring_block

    block = tier_m_authoring_block()
    assert "manage" in block and "sync_restart" in block
    assert "directly from its own session" in block
    assert "Never" in block and "git_integration_worker" in block
    assert "Do not fire" in block and "contract:propagate" in block
    assert "cannot (or should not) call" not in block
    assert "via\ncursor-auto" not in block


def test_skill_surface_introspects_instead_of_asserting_loaded() -> None:
    """Chips are a request; seat must introspect and self-fetch gaps."""
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    for slug in MISSION_SKILL_SLUGS:
        assert f"`{slug}`" in out
    assert "Use the `" in out
    assert "`<slug>` skill" in out
    assert "complete set attachable" not in out
    for slug in (
        "operator-proxy-substrate",
        "claude-ai-cdp-navigation",
        "path-sim",
    ):
        assert f"`{slug}`" in out
    assert "decision:operator-proxy-skill-surface-split" in out
    assert "request**, not" in out or "request, not a receipt" in out.lower()


_RUNBOOK_FIXTURE = """\
# Maestro loop

## Trigger
First commission must not wait.

## Refuse
The first commission is not `contract=none` on birth.

## Steps
1. Read Steps only at act time.
"""


def _hop_block(out: str) -> str:
    start = out.index(HOP_STATUS_MARKER)
    end = out.index("## Mission seat map (BINDING")
    return out[start:end]


def test_hop_block_inlines_refuse_section_from_runbook_bytes() -> None:
    with mock.patch(
        "claude_bundles.operator_proxy_mission.load_maestro_runbook",
        return_value=(_RUNBOOK_FIXTURE, ""),
    ):
        out = ensure_operator_proxy_mission_prompt("# Mission\n")
    block = _hop_block(out)
    assert "First commission must not wait." in block
    assert "contract=none" in block
    digest = hashlib.sha256(_RUNBOOK_FIXTURE.encode()).hexdigest()
    assert f"resolved sha256={digest}" in block
    for excerpt in ("First commission must not wait.", "contract=none"):
        assert not re.search(r"^## ", excerpt, re.MULTILINE)


def test_refuse_excerpt_is_small_against_briefing_block() -> None:
    refuse_len = len(extract_sections(_RUNBOOK_FIXTURE, ("Refuse",)))
    assert refuse_len < len(_BRIEFING_BLOCK)


def test_refuse_contract_none_in_hop_block_not_in_success_condition() -> None:
    with mock.patch(
        "claude_bundles.operator_proxy_mission.load_maestro_runbook",
        return_value=(_RUNBOOK_FIXTURE, ""),
    ):
        out = ensure_operator_proxy_mission_prompt("# Mission\n")
    block = _hop_block(out)
    sc_line = next(ln for ln in block.splitlines() if ln.startswith("- success-condition:"))
    assert "contract=none" not in sc_line
    assert "2000 characters" not in sc_line
    assert block.count("contract=none") >= 1


def test_fresh_mission_prompt_resolves_maestro_runbook() -> None:
    with mock.patch(
        "claude_bundles.operator_proxy_mission.load_maestro_runbook",
        return_value=(_RUNBOOK_FIXTURE, ""),
    ):
        out = ensure_operator_proxy_mission_prompt("# Mission\n")
    block = _hop_block(out)
    assert "fetch-decision: runbook:maestro-loop resolved sha256=" in block
    assert (
        "fetch-decision: skill:retrieval-before-authoring skipped "
        "reason=chip_requested"
        in block
    )
    assert "not_resolvable_by_composer" not in block
    assert "runbook:maestro-loop skipped reason=not_in_context" not in block


# Render lengths for TYPE: CONTINUITY_HANDOFF + # body (2026-10-02):
# - pre-trim baseline: 42031 (measured opening)
# - lane-14181 @ cdc14894a (before slash removal): 42019
_PRE_CDC14894A_MISSION_PROMPT_LEN = 42019
_DISTINCTIVE_REFUSE_SENTENCE = "Re-arming `send_later` as a heartbeat."
_RUNBOOK_REFUSE_FIXTURE_PATH = (
    Path(__file__).resolve().parent / "testdata" / "maestro_runbook_refuse_2124_fixture.md"
)


def _runbook_body_for_opening_trim() -> str:
    from claude_bundles.maestro_runbook_load import load_maestro_runbook

    body, _err = load_maestro_runbook()
    if body:
        return body
    return _RUNBOOK_REFUSE_FIXTURE_PATH.read_text(encoding="utf-8")


def test_operator_opening_trim_metrics_with_real_runbook() -> None:
    """Post-trim must be ≥2000 below cdc14894a (42019); post-amend records new len."""
    from claude_bundles.runbook_excerpt import extract_sections

    body = _runbook_body_for_opening_trim()
    refuse_len = len(extract_sections(body, ("Refuse",)))
    assert refuse_len >= 2000
    with mock.patch(
        "claude_bundles.operator_proxy_mission.load_maestro_runbook",
        return_value=(body, ""),
    ):
        out = ensure_operator_proxy_mission_prompt("TYPE: CONTINUITY_HANDOFF\n# body\n")
    post_len = len(out)
    assert post_len <= _PRE_CDC14894A_MISSION_PROMPT_LEN - 2000
    block = _hop_block(out)
    sc_line = next(ln for ln in block.splitlines() if ln.startswith("- success-condition:"))
    assert _DISTINCTIVE_REFUSE_SENTENCE in block
    assert _DISTINCTIVE_REFUSE_SENTENCE not in sc_line
    assert block.count(_DISTINCTIVE_REFUSE_SENTENCE) == 1
    for needle in (
        "2000 characters",
        "poll_hint",
        "contract=conductor",
        "git_integration_worker",
        "porcelain_raw_open",
        "send_later",
        "successor_birth_id",
    ):
        assert needle in out
    assert "not_resolvable_by_composer" not in out
    assert out.count("2026-09-29: Fable credits near spent") == 1
    briefing = _BRIEFING_BLOCK
    assert briefing.count("2026-09-29: Fable credits near spent") == 1
    assert "contract=none" not in briefing.replace(
        "Do not admit one with `contract=none`.", ""
    )
    assert not out.startswith("/")
    assert "`retrieval-before-authoring`" in out
    assert "investigate`, `freeform`, and `conductor`" in out


def test_stage_operator_proxy_omits_slash_keeps_use_line_authority() -> None:
    from claude_bundles.cdp_model_endpoint_staging import stage_cdp_prompt_with_skills

    exec_id = "test-exec-amend-14181"
    staged = stage_cdp_prompt_with_skills(
        execution_id=exec_id,
        prompt_text="TYPE: CONTINUITY_HANDOFF\n# body\n",
        purpose="operator-proxy",
    )
    assert staged.staged
    from claude_bundles.cdp_model_endpoint_staging import ephemeral_dir

    merged = (ephemeral_dir(exec_id) / "prompt.md").read_text(encoding="utf-8")
    assert not merged.lstrip().startswith("/cdp-operator-proxy")
    marker, _rest = merged.split("-->", 1)
    assert marker.startswith("<!--cdp-required-skills:")
    for slug in MISSION_SKILL_SLUGS:
        assert slug in marker


def test_mission_prompt_runbook_missing_still_has_success_condition() -> None:
    with mock.patch(
        "claude_bundles.operator_proxy_mission.load_maestro_runbook",
        return_value=(None, "unreachable"),
    ):
        out = ensure_operator_proxy_mission_prompt("# Mission\n")
    block = _hop_block(out)
    assert any(ln.startswith("- success-condition:") for ln in block.splitlines())
    assert "fetch-decision: runbook:maestro-loop skipped reason=unreachable" in block


def test_cdp_operator_proxy_skill_keep_alive_stale_text_absent() -> None:
    from pathlib import Path

    skill_path = (
        Path(__file__).resolve().parents[2]
        / "cursor-plugins/ulg-ecosystem/skills/cdp-operator-proxy/SKILL.md"
    )
    text = skill_path.read_text(encoding="utf-8")
    assert "Arm-and-re-arm" not in text
    assert "re-arm every turn" not in text
    assert "Re-arm this wake before the turn ends" not in text
    assert "Do not arm Monitor" in text


_WAKE_BRIEF_SHA256 = (
    "4ebc236345294b82eb3332a8fb1153b356e020c9d75ad5ae2b583a3aba5c4ea0"
)


def test_wake_brief_unchanged_do_not_arm_monitor() -> None:
    from claude_bundles.operator_proxy_wake_brief import wake_briefing_paragraph

    text = wake_briefing_paragraph()
    assert "Do not arm Monitor" in text
    assert hashlib.sha256(text.encode()).hexdigest() == _WAKE_BRIEF_SHA256
