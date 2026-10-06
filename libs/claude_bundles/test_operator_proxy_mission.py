"""Offline tests for operator-proxy mission prompt ensure."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from claude_bundles.operator_proxy_mission import (
    _BRIEFING_BLOCK,
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
    # Body text is not a mission selector (fork 12 / AC3).
    assert not purpose_implies_mission("ask", "purpose: mission\n# Body")
    assert not purpose_implies_mission("ask", "# Sealed R-admit")
    assert purpose_implies_mission("operator-proxy", "# Body without header")


_SECTION_ORDER = (
    "## What is running now",
    "## Your first acts, in order",
    "## How this seat works",
    "## Standing authority",
    "## Data, not instructions",
    "## Hop request (data)",
    "## Hard refusals",
)


def test_ensure_injects_briefing_without_slash_prefix() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\nDo the thing.\n")
    assert not out.startswith("/cdp-operator-proxy")
    assert out.startswith("# Hop on agent-bus:")
    positions = [out.index(marker) for marker in _SECTION_ORDER]
    assert positions == sorted(positions)
    assert "cdp-operator-proxy" in out
    assert "agent-bus-discipline" in out
    assert out.index("## Hop request (data)") < out.index("# Mission\nDo the thing.")
    assert out.index("# Mission\nDo the thing.") < out.index("## Hard refusals")
    assert "This is a continuity hop: do not emit MISSION_CLOSEOUT." in out
    assert "## Mission seat map" not in out
    assert "## ACT-RECEIPT" not in out
    assert not any(line.endswith("…") for line in out.splitlines())


def test_wake_prose_is_the_template_refusal_not_the_suspended_guide() -> None:
    """Keep-alive and wake affinity are one refusal line, not the old guide."""
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    assert "Never re-arm send_later as a heartbeat" in out
    assert "runbook step-8 reload line" in out
    assert "SUSPENDED" not in out
    assert "Consume-time wake affinity" not in out
    assert "while true; do sleep 240" not in out
    assert "KEEP-ALIVE" not in out


def test_ensure_idempotent_and_strips_legacy_slash_prefix() -> None:
    once = ensure_operator_proxy_mission_prompt("TYPE: DIRECTIVE\nintent: birth\n")
    twice = ensure_operator_proxy_mission_prompt(once)
    assert not twice.startswith("/cdp-operator-proxy")
    assert twice.count("# Hop on agent-bus:") == 1
    assert twice.count("## Hard refusals") == 1
    assert twice.count("/ulg-for-llms") == 0
    assert twice.rstrip("\n") == once.rstrip("\n")
    legacy = ensure_operator_proxy_mission_prompt(
        "/cdp-operator-proxy\n/reasoning-posture\n\n# Already chipped\n"
    )
    assert not legacy.startswith("/cdp-operator-proxy")
    assert "# Already chipped" in legacy
    assert legacy.startswith("# Hop on agent-bus:")


def _mission_delivery_prefix(*, marker_only: bool = False, use_lines_only: bool = False) -> str:
    from claude_bundles.cowork_skill_delivery import (
        format_cdp_use_the_lines,
        render_cdp_required_authority,
    )

    marker = render_cdp_required_authority(list(MISSION_SKILL_SLUGS))
    use_lines = format_cdp_use_the_lines(list(MISSION_SKILL_SLUGS))
    if marker_only:
        return marker
    if use_lines_only:
        return use_lines
    return marker + use_lines


def _assert_single_hop_briefing(text: str) -> None:
    assert text.count("# Hop on agent-bus:") == 1
    assert text.count("## Hard refusals") == 1


def test_ensure_idempotent_past_sealed_marker_and_use_lines() -> None:
    sidecar = "## Settled\nfrom mock\n"
    body = "TYPE: CONTINUITY_HANDOFF\nthread_id: 12286\n"
    once = ensure_operator_proxy_mission_prompt(
        body,
        standing_handoff_text=sidecar,
    )
    prefixed = _mission_delivery_prefix() + once
    twice = ensure_operator_proxy_mission_prompt(prefixed, standing_handoff_text=sidecar)
    assert twice == prefixed.strip()
    _assert_single_hop_briefing(twice)
    assert "<!--cdp-required-skills:" in twice
    for slug in MISSION_SKILL_SLUGS:
        assert f"Use the `{slug}` skill" in twice


def test_ensure_idempotent_past_sealed_marker_only() -> None:
    sidecar = "## Settled\nfrom mock\n"
    body = "TYPE: CONTINUITY_HANDOFF\nthread_id: 12286\n"
    once = ensure_operator_proxy_mission_prompt(
        body,
        standing_handoff_text=sidecar,
    )
    prefixed = _mission_delivery_prefix(marker_only=True) + once
    twice = ensure_operator_proxy_mission_prompt(prefixed, standing_handoff_text=sidecar)
    assert twice == prefixed.strip()
    _assert_single_hop_briefing(twice)


def test_ensure_idempotent_past_use_lines_only() -> None:
    sidecar = "## Settled\nfrom mock\n"
    body = "TYPE: CONTINUITY_HANDOFF\nthread_id: 12286\n"
    once = ensure_operator_proxy_mission_prompt(
        body,
        standing_handoff_text=sidecar,
    )
    prefixed = _mission_delivery_prefix(use_lines_only=True) + once
    twice = ensure_operator_proxy_mission_prompt(prefixed, standing_handoff_text=sidecar)
    assert twice == prefixed.strip()
    _assert_single_hop_briefing(twice)


def test_stage_then_resolve_prompt_does_not_double_hop_briefing(
    tmp_path, monkeypatch
) -> None:
    """Production path: staging ensure + resolve_prompt ensure stays single briefing."""
    from cdp_ask.models import SubmitProjectAskRequest
    from cdp_ask.runner import resolve_prompt
    from claude_bundles.cdp_model_endpoint_staging import (
        ephemeral_dir,
        stage_cdp_prompt_with_skills,
    )

    root = tmp_path / "cortex_files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    exec_id = "exec-hop-double-ensure"
    staged = stage_cdp_prompt_with_skills(
        execution_id=exec_id,
        prompt_text="TYPE: CONTINUITY_HANDOFF\nthread_id: 12286\n",
        purpose="operator-proxy",
        skills=["ulg-architecture"],
    )
    assert staged.staged
    staged_text = (ephemeral_dir(exec_id) / "prompt.md").read_text(encoding="utf-8")
    assert "<skills_inline>" in staged_text
    marker_at = staged_text.index("<!--cdp-required-skills:")
    assert marker_at < staged_text.index("# Hop on agent-bus:")
    assert staged_text[marker_at:].startswith("<!--cdp-required-skills:")
    req = SubmitProjectAskRequest(
        prompt_uri=staged.prompt_uri,
        purpose="operator-proxy",
        stargate_execution_id=exec_id,
    )
    with patch(
        "claude_bundles.operator_proxy_hop_status.standing_handoff_path"
    ) as path:
        path.return_value.is_file.return_value = False
        submitted = resolve_prompt(req)[0]
    _assert_single_hop_briefing(submitted)
    assert submitted.rstrip("\n") == staged_text.rstrip("\n")
    assert "TYPE: CONTINUITY_HANDOFF" in submitted


def test_ensure_preserves_author_use_line_under_hop_request_data() -> None:
    body = (
        "Use the `conductor` skill\n"
        "TYPE: CONTINUITY_HANDOFF\nthread_id: 12286\n"
    )
    out = ensure_operator_proxy_mission_prompt(body)
    hop_data = out.split("## Hop request (data)", 1)[1].split("## Hard refusals", 1)[0]
    assert "Use the `conductor` skill" in hop_data
    assert "TYPE: CONTINUITY_HANDOFF" in hop_data


def test_ensure_no_prefix_matches_pre_peek_render() -> None:
    """Unprefixed bodies render once; caller data stays under Hop request (data)."""
    body = "TYPE: CONTINUITY_HANDOFF\nthread_id: 12286\n"
    out = ensure_operator_proxy_mission_prompt(body)
    assert out.count("# Hop on agent-bus:") == 1
    hop_data = out.split("## Hop request (data)", 1)[1].split("## Hard refusals", 1)[0]
    assert body.strip() in hop_data
    assert ensure_operator_proxy_mission_prompt(out).rstrip("\n") == out.rstrip("\n")


def test_ensure_idempotent_past_delivery_prefix_crlf_use_lines() -> None:
    sidecar = "## Settled\nfrom mock\n"
    body = "TYPE: CONTINUITY_HANDOFF\nthread_id: 12286\n"
    once = ensure_operator_proxy_mission_prompt(body, standing_handoff_text=sidecar)
    marker = _mission_delivery_prefix(marker_only=True)
    use_lines = _mission_delivery_prefix(use_lines_only=True).replace("\n", "\r\n")
    prefixed = marker + use_lines + once
    twice = ensure_operator_proxy_mission_prompt(prefixed, standing_handoff_text=sidecar)
    assert twice == prefixed.strip()
    _assert_single_hop_briefing(twice)


def test_stage_resolve_single_briefing_with_house_pool_inline(
    tmp_path, monkeypatch
) -> None:
    """House block + inline must_load on continuity card — one hop briefing."""
    from agent_bus_store.test_house_pools import _MANIFEST_BLOCK

    from cdp_ask.models import SubmitProjectAskRequest
    from cdp_ask.runner import resolve_prompt
    from claude_bundles.cdp_model_endpoint_staging import stage_cdp_prompt_with_skills

    root = tmp_path / "cortex_files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    card_path = root / "notes/system/threads/12286-continuity.md"
    card_path.parent.mkdir(parents=True)
    card_path.write_text(f"# card\n\n{_MANIFEST_BLOCK}\n", encoding="utf-8")
    exec_id = "exec-hop-house-inline"
    staged = stage_cdp_prompt_with_skills(
        execution_id=exec_id,
        prompt_text="TYPE: CONTINUITY_HANDOFF\nthread_id: 12286\n",
        purpose="operator-proxy",
        dispatch_thread_id="12286",
        skills=["ulg-architecture"],
    )
    assert staged.staged
    req = SubmitProjectAskRequest(
        prompt_uri=staged.prompt_uri,
        purpose="operator-proxy",
        stargate_execution_id=exec_id,
    )
    with patch(
        "claude_bundles.operator_proxy_hop_status.standing_handoff_path"
    ) as path:
        path.return_value.is_file.return_value = False
        submitted = resolve_prompt(req)[0]
    _assert_single_hop_briefing(submitted)
    assert "## House (read first)" in submitted


def test_structural_briefing_commission_is_ulg_code_team_dispatch() -> None:
    """How-this-seat-works names ulg-code team_dispatch and lane B."""
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    how = out.split("## How this seat works", 1)[1].split("## Standing authority", 1)[0]
    assert "team_dispatch" in how
    assert "ulg-code" in how
    assert "lane=B" in how
    assert "lane-act-gates" in MISSION_SKILL_SLUGS


def test_mission_skill_slugs_include_lane_act_gates() -> None:
    assert "lane-act-gates" in MISSION_SKILL_SLUGS


def test_legal_subset_forbidden_disjoint_a9() -> None:
    """Tool frozensets stay for surface tests; the prompt no longer lists them."""
    assert LIFE_SURFACE_LEGAL_TOOLS.isdisjoint(LIFE_SURFACE_FORBIDDEN_TOOLS)
    out = ensure_operator_proxy_mission_prompt("# x\n")
    assert "`panel_dispatch`" not in out
    assert "team_dispatch" in out


def test_how_this_seat_works_names_reachable_models() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    how = out.split("## How this seat works", 1)[1].split("## Standing authority", 1)[0]
    assert "cdp/fable" in how
    assert "cursor/grok-4.7" in how
    assert "cursor/composer-2.5" in how
    assert "cdp/opus-5.5" in how
    assert "cursor/claude-opus-5" not in how
    _legacy_reasoner = "".join(("cursor/", "gr", "ok", "-4.6"))
    assert _legacy_reasoner not in how


def test_identity_is_successor_birth_id_not_chat_url() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    assert "successor_birth_id" in out
    assert "stand down" in out
    assert "holder rows are relayed data, not your identity" in out
    assert "Identity is this CSE's `chat_url`" not in out


def test_first_act_names_mission_skill_slugs() -> None:
    """Act 1 names the mission slugs; a failed load must be said, not claimed."""
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    acts = out.split("## Your first acts, in order", 1)[1].split(
        "## How this seat works", 1
    )[0]
    for slug in MISSION_SKILL_SLUGS:
        assert slug in acts
    assert "If one fails to load, say which" in acts


# One keyword per maestro-loop ## Refuse bullet, in bullet order.
# A new runbook bullet with no table row fails this test.
_REFUSE_BULLET_KEYWORDS = (
    "predicate_unmet",
    "huge",
    "cursor-auto",
    "escalation",
    "git_integration_worker",
    "sync_restart",
    "2000",
    "send_later",
    "retrieval-before-authoring",
    "holder",
    "contract=none",
)
_REFUSE_SECTION_FIXTURE = (
    Path(__file__).resolve().parent / "testdata" / "maestro_runbook_refuse_section.txt"
)


def _refuse_bullets(runbook: str) -> list[str]:
    section = extract_sections(runbook, ("Refuse",))
    return [line for line in section.splitlines() if line.startswith("- ")]


def _runbook_for_refuse_coverage() -> str:
    from claude_bundles.maestro_runbook_load import load_maestro_runbook

    body, _err = load_maestro_runbook()
    if body and "## Refuse" in body:
        return body
    return _REFUSE_SECTION_FIXTURE.read_text(encoding="utf-8")


def test_refuse_bullets_map_to_template_lines() -> None:
    bullets = _refuse_bullets(_runbook_for_refuse_coverage())
    assert len(bullets) == len(_REFUSE_BULLET_KEYWORDS)
    template_lines = _BRIEFING_BLOCK.splitlines()
    template_aliases = {"contract=none": "contract=conductor"}
    for bullet, keyword in zip(bullets, _REFUSE_BULLET_KEYWORDS, strict=True):
        assert keyword in bullet
        template_key = template_aliases.get(keyword, keyword)
        assert any(template_key in line for line in template_lines)


def test_rendered_prompt_is_under_the_old_briefing() -> None:
    """Fixed template plus caller body stays far under the pre-v2 render."""
    out = ensure_operator_proxy_mission_prompt("TYPE: CONTINUITY_HANDOFF\n# body\n")
    assert len(out.encode()) < 12000
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
    assert not any(line.endswith("…") for line in out.splitlines())
    assert not out.startswith("/")


def _stage_under(tmp_path, monkeypatch, **kwargs):
    from claude_bundles import cdp_model_endpoint_staging as staging

    monkeypatch.setattr(staging, "ephemeral_dir", lambda _eid: tmp_path)
    monkeypatch.setattr(staging, "cortex_files_root", lambda: tmp_path)
    return staging.stage_cdp_prompt_with_skills(execution_id="ignored", **kwargs)


def _induction_slugs(merged: str) -> list[str]:
    from claude_bundles.cowork_skill_delivery import (
        extract_cdp_required_authority,
        partition_cdp_skills,
    )

    authority = extract_cdp_required_authority(merged)
    assert authority is not None
    return partition_cdp_skills(authority)[0]


def test_stage_freeform_body_purpose_does_not_induce_mission_skills(
    tmp_path, monkeypatch
) -> None:
    """job=freeform with a quoted body purpose line does not get mission chips."""
    staged = _stage_under(
        tmp_path,
        monkeypatch,
        prompt_text="handoff\npurpose: operator-proxy\n",
        purpose="freeform",
    )
    assert staged.staged
    merged = (tmp_path / "prompt.md").read_text(encoding="utf-8")
    assert not merged.lstrip().startswith("/")
    induction = _induction_slugs(merged)
    assert "cdp-operator-proxy" not in induction


def test_staged_prompt_text_runner_does_not_imply_mission_from_body(
    tmp_path, monkeypatch
) -> None:
    """Staging and the runner share wire-only mission selection.

    purpose=freeform; line 2 of the author body is column-0 ``purpose: operator-proxy``.
    Neither ``staged.mission`` nor ``purpose_implies_mission`` treat that as a mission.
    """
    staged = _stage_under(
        tmp_path,
        monkeypatch,
        prompt_text="handoff\npurpose: operator-proxy\n",
        purpose="freeform",
    )
    assert staged.staged
    merged = (tmp_path / "prompt.md").read_text(encoding="utf-8")
    assert staged.mission is False
    assert not purpose_implies_mission("freeform", merged)


def test_body_purpose_header_does_not_set_mission_after_seal(
    tmp_path, monkeypatch
) -> None:
    """A column-0 purpose header in the author body is not a mission after seal."""
    body = ("\n" * 39) + "purpose: operator-proxy\n"
    staged = _stage_under(
        tmp_path,
        monkeypatch,
        prompt_text=body,
        purpose="freeform",
    )
    assert staged.staged
    merged = (tmp_path / "prompt.md").read_text(encoding="utf-8")
    assert staged.mission is False
    assert purpose_implies_mission("freeform", merged) is False


def test_inline_skills_do_not_make_body_purpose_a_mission(
    tmp_path, monkeypatch
) -> None:
    """Inline skills plus a body purpose line still leave mission False on freeform."""
    body = "line1\nline2\nline3\nline4\npurpose: operator-proxy\n"
    staged = _stage_under(
        tmp_path,
        monkeypatch,
        prompt_text=body,
        purpose="freeform",
        skills=["investigation-economy"],
    )
    assert staged.staged
    merged = (tmp_path / "prompt.md").read_text(encoding="utf-8")
    assert '<skill slug="investigation-economy"' in merged
    assert staged.mission is False
    assert purpose_implies_mission("freeform", merged) is False


def test_stage_operator_proxy_omits_slash_keeps_use_line_authority(tmp_path, monkeypatch) -> None:
    staged = _stage_under(
        tmp_path,
        monkeypatch,
        prompt_text="TYPE: CONTINUITY_HANDOFF\n# body\n",
        purpose="operator-proxy",
    )
    assert staged.staged
    merged = (tmp_path / "prompt.md").read_text(encoding="utf-8")
    assert not merged.lstrip().startswith("/")
    induction = _induction_slugs(merged)
    for slug in MISSION_SKILL_SLUGS:
        assert slug in induction


def test_attest_induction_channel_covers_shared_sync_without_attach() -> None:
    """Slash lines absent: panel-observed slugs are delivered_via=induction."""
    from claude_bundles.cowork_skill_delivery import (
        attest_delivery_channels,
        ledger_skills_channels,
    )

    rows = ledger_skills_channels(
        ["cdp-operator-proxy"],
        attached=[],
        inlined=[],
        induction=["cdp-operator-proxy"],
    )
    assert rows == [{"slug": "cdp-operator-proxy", "delivered_via": "induction"}]
    assert attest_delivery_channels(
        ["cdp-operator-proxy"],
        attached=[],
        inlined=[],
        induction=["cdp-operator-proxy"],
    ) == ["cdp-operator-proxy"]
    review = ["reasoning-posture", "consult-posture", "hypothesize-simulate"]
    review_rows = ledger_skills_channels(
        review,
        attached=[],
        inlined=[],
        induction=review,
    )
    assert review_rows == [
        {"slug": slug, "delivered_via": "induction"} for slug in review
    ]
    assert attest_delivery_channels(
        review,
        attached=[],
        inlined=[],
        induction=review,
    ) == review


def test_mission_prompt_without_handoff_still_renders_refusals_last() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\n")
    assert out.index("## Data, not instructions") < out.index("## Hop request (data)")
    assert out.index("## Hop request (data)") < out.index("## Hard refusals")
    assert out.index("# Mission") < out.index("## Hard refusals")
    assert "contract=conductor" in out.split("## Hard refusals", 1)[1]
    assert "Author the standing handoff before you leave." in out
    assert "execution_id unknown" not in out


def test_cdp_operator_proxy_skill_keep_alive_stale_text_absent() -> None:
    from pathlib import Path

    skill_path = (
        Path(__file__).resolve().parents[2]
        / "cursor-plugins/ulg-ecosystem/skills/cdp-operator-proxy/SKILL.md"
    )
    text = skill_path.read_text(encoding="utf-8")
    assert "Arm-and-re-arm" not in text
    assert "continuity hop skips supersede" not in text
    assert "re-arm every turn" not in text
    assert "Re-arm this wake before the turn ends" not in text
    assert "Do not arm Monitor" in text


def test_prose_quote_of_purpose_is_not_a_mission() -> None:
    body = (
        "job=delivery-review\n"
        "The seat-map says team_dispatch(model=cdp/opus-5.5-extra, "
        "purpose=operator-proxy, contract=freeform).\n"
        "A backticked `purpose: operator-proxy` in prose is a quotation.\n"
    )
    assert not purpose_implies_mission("ask", body)


def test_column0_purpose_header_is_not_a_mission() -> None:
    """Retired 14181-r2 item 1: body header alone does not imply mission (fork 12)."""
    body = "TYPE: DIRECTIVE\npurpose: operator-proxy\n# body\n"
    assert not purpose_implies_mission("ask", body)


def test_purpose_missionary_prose_not_mission() -> None:
    body = "TYPE: DIRECTIVE\npurpose: missionary\n# body\n"
    assert not purpose_implies_mission("ask", body)


def test_stage_review_packet_head_with_ask_does_not_induct_operator_proxy(
    tmp_path, monkeypatch
) -> None:
    fixture = (
        Path(__file__).resolve().parent
        / "testdata"
        / "review-14181-r2-delta-packet-20261002-head60.txt"
    )
    staged = _stage_under(
        tmp_path,
        monkeypatch,
        prompt_text=fixture.read_text(encoding="utf-8"),
        purpose="ask",
    )
    assert staged.staged
    merged = (tmp_path / "prompt.md").read_text(encoding="utf-8")
    assert "cdp-operator-proxy" not in _induction_slugs(merged)
