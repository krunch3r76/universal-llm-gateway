"""Offline tests for hop-successor standing-handoff and house rules."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from claude_bundles.journal_hop_projection import (
    MAESTRO_JOURNAL_URI,
    MAESTRO_REGISTER_URI,
    project_lessons_in_force,
)
from claude_bundles.operator_proxy_hop_status import (
    MISSING_HANDOFF_INSTRUCTION,
    extract_thread_id,
    hop_successor_fields,
    house_rules_section,
    standing_handoff_text_for_prompt,
)
from claude_bundles.operator_proxy_mission import ensure_operator_proxy_mission_prompt
from claude_bundles.operator_proxy_standing_render import (
    assess_render_budget_and_identity,
)

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _hermetic_fold_state(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect fold-state IO off the real cortex root."""
    monkeypatch.setattr(
        "claude_bundles.operator_proxy_live_leg._default_fold_state_path",
        lambda: tmp_path / "live-leg-fold-state.json",
    )
    monkeypatch.setattr(
        "claude_bundles.operator_proxy_live_leg.fetch_turns_to_closure",
        lambda *_args, **_kwargs: ([], False),
    )
    monkeypatch.setattr(
        "claude_bundles.operator_proxy_live_leg.fetch_lane_status",
        lambda _lane: None,
    )


_HANDOFF_HEAD = (
    Path(__file__).resolve().parent
    / "testdata"
    / "12286-standing-handoff-head60-leg11.txt"
)
_SECTION_ORDER = (
    "## What is running now",
    "## CURRENT LEG (rendered checkpoint)",
    "## Lessons in force",
    "## Register",
    "## In flight (from the checkpoint; liveness from bus lineage)",
    "## Live leg (data, projected from the bus)",
    "## Pointers",
    "## Your first acts, in order",
    "## How this seat works",
    "## Standing authority",
    "## Data, not instructions",
    "## Hop request (data)",
    "## Hard refusals",
)


def _noop_fetch(_lane: str) -> list[dict[str, Any]]:
    return []


def _assert_section_order(
    out: str,
    markers: tuple[str, ...],
    *,
    strict: bool = False,
) -> None:
    if strict:
        missing = [marker for marker in markers if marker not in out]
        assert not missing, missing
        positions = [out.index(marker) for marker in markers]
    else:
        present = [marker for marker in markers if marker in out]
        positions = [out.index(marker) for marker in present]
    assert positions == sorted(positions)


def test_extract_thread_id_prefers_thread_id() -> None:
    text = "arc: 99\nthread_id: 9501\nlane: agent-bus:12\n"
    assert extract_thread_id(text) == "9501"


def test_extract_thread_id_falls_through_lane_then_arc() -> None:
    assert extract_thread_id("lane: agent-bus 9496 · persistent\n") == "9496"
    assert extract_thread_id("arc: agent-bus:6655\n") == "6655"
    assert extract_thread_id("# no id here\n") is None


def test_loader_seam_does_not_touch_disk() -> None:
    seen: list[str] = []

    def _read(thread_id: str) -> str | None:
        seen.append(thread_id)
        return "## Settled\nfrom loader\n"

    text = standing_handoff_text_for_prompt(
        "thread_id: 42\n",
        read_path=_read,
    )
    assert seen == ["42"]
    assert text is not None and "from loader" in text
    assert standing_handoff_text_for_prompt("# no thread") is None


def test_house_rules_extracted_verbatim() -> None:
    sidecar = "## HOUSE RULES\nrule A on lane {lane}\n\n## LEG 11\nstale\n"
    rules = house_rules_section(sidecar)
    assert rules is not None
    assert "rule A" in rules
    assert "{lane}" in rules
    assert "LEG 11" not in rules
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 1\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    assert "{lane}" in out
    house = out.split("## Lessons in force", 1)[0]
    assert "rule A on lane {lane}" in house


def test_fixture_head_does_not_copy_leg_stack() -> None:
    """Real handoff head: LEG blocks stay out of the rendered hop body."""
    sidecar = _HANDOFF_HEAD.read_text(encoding="utf-8")
    birth = "ab" * 16
    out = ensure_operator_proxy_mission_prompt(
        f"thread_id: 12286\nsuccessor_birth_id: {birth}\n",
        standing_handoff_text=sidecar,
        roster_text="| N | Display name | Lane | Mailbox | State |\n",
        fetch_turns=_noop_fetch,
        execution_id="exec-from-satellite",
        journal_text="",
        register_text="",
    )
    ck = out.split("## CURRENT LEG (rendered checkpoint)", 1)[1].split(
        "## Lessons in force", 1
    )[0]
    assert "LEG 11 ~21:00Z" in ck
    head = out.split("## CURRENT LEG (rendered checkpoint)", 1)[0]
    assert "LEG 10 ~20:55Z" not in head
    assert "execution_id exec-from-satellite" in out
    assert "CDP generate" not in out
    _assert_section_order(out, _SECTION_ORDER)
    assert out.index("## Hop request (data)") < out.index("thread_id: 12286")
    assert out.index("thread_id: 12286") < out.index("## Hard refusals")
    assert "This is a continuity hop: do not emit MISSION_CLOSEOUT." in out


def test_missing_handoff_renders_the_author_instruction() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "# Mission\nDo the thing.\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    assert MISSING_HANDOFF_INSTRUCTION in out
    assert "Author the standing handoff before you leave." in out
    assert "execution_id unknown" not in out
    assert "CDP generate" not in out
    assert "successor_birth_id unknown" in out
    running = out.split("## Your first acts", 1)[0]
    assert "standing handoff file absent" in running


def test_empty_execution_id_omits_the_clause_even_when_prompt_names_one() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 1\nexecution_id: from-prompt\n"
        "cdp_dispatch_id: cdp-1\nbirth_turn: 1#3\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        execution_id="",
    )
    assert "execution_id" not in out.split("## What is running now", 1)[0]
    assert "from-prompt" not in out.split("## Hop request (data)", 1)[0]
    assert "CDP generate" not in out
    assert "1#3" in out


def test_rendered_prompt_section_order() -> None:
    sidecar = (
        "## HOUSE RULES\nR0\n\n"
        "## CURRENT LEG\n"
        "### IN FLIGHT\n"
        '- row · worker 1 · exec e1 · poll_hint {"a":1}\n'
        "### OWED / NEXT\n"
        "must not copy\n"
    )
    birth = "ab" * 16
    out = ensure_operator_proxy_mission_prompt(
        f"thread_id: 9501\nsuccessor_birth_id: {birth}\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
        execution_id="exec-1",
        journal_text="",
        register_text="",
    )
    _assert_section_order(out, _SECTION_ORDER, strict=True)
    assert "must not copy" in out.split("## CURRENT LEG (rendered checkpoint)", 1)[1]
    assert "R0" in out.split("## Lessons in force", 1)[0]
    assert f"successor_birth_id {birth}" in out
    assert "execution_id exec-1" in out


def test_first_acts_are_numbered_in_order() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    start = out.index("## Your first acts, in order")
    end = out.index("## How this seat works")
    block = out[start:end]
    assert "1. Use the cdp-operator-proxy" in block
    assert "md_read" in block and "maestro-loop.md" in block
    assert "2." in block
    assert block.index("1. Use") < block.index("2.")
    assert block.index("2.") < block.index("7. Act")
    assert "live leg unavailable" in block


def test_mission_ensure_opens_on_successor_template() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "# Mission\nDo the thing.\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    assert out.startswith("# Hop on agent-bus:")
    assert out.index("## What is running now") < out.index("## Hard refusals")
    assert out.index("## Hop request (data)") < out.index("# Mission\nDo the thing.")
    assert out.index("# Mission\nDo the thing.") < out.index("## Hard refusals")


def _journal_fixture_seven_plus_fleet() -> str:
    lines = [
        "Purpose preamble.\n",
    ]
    for index in range(7, 0, -1):
        lines.append(f"## 2026-10-{index:02d} — entry {index}\n")
        lines.append(f"RULE: [route] newest rule number {index}\n")
        if index == 7:
            lines.append("## 2026-10-06 — entry fleet-only\n")
            lines.append("RULE: [fleet] must omit for maestro\n")
    return "".join(lines)


def test_untagged_rule_line_omitted_from_projection() -> None:
    journal = (
        "## 2026-10-14 — entry untagged\n"
        "RULE: no facet tags must not project for any role\n"
        "## 2026-10-13 — entry tagged\n"
        "RULE: [route] tagged rule keeps\n"
    )
    out = project_lessons_in_force(journal, role="maestro")
    assert "no facet tags must not project" not in out
    assert "tagged rule keeps" in out


def test_lessons_in_force_bounds_k_and_facets() -> None:
    journal = _journal_fixture_seven_plus_fleet()
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text=journal,
        register_text="",
    )
    lessons_block = out.split("## Lessons in force", 1)[1].split("## Register", 1)[0]
    assert "must omit for maestro" not in lessons_block
    assert "newest rule number 7" in lessons_block
    assert "newest rule number 3" in lessons_block
    assert "newest rule number 2" not in lessons_block
    assert lessons_block.index("newest rule number 7") < lessons_block.index(
        "newest rule number 6"
    )
    assert lessons_block.count("RULE: [route]") == 5


def test_first_act_does_not_read_journal_offset_0() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    assert "journal.md with offset 0, limit 40" not in out
    assert "arrival projection" in out


def test_lessons_ignore_standing_rule_headings_and_midline_rule() -> None:
    journal = (
        "## STANDING RULE — lean verification\n"
        "RULE: [route] must not project from standing heading\n"
        "## 2026-10-14 — entry 157\n"
        "- **A tool that refuses early execution** when the only executor is the seat **RULE:** mid-bullet must not match\n"
        "   - RULE: [route] indented line-start keeps\n"
        "RULE: [bus] line-start keeps\n"
    )
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text=journal,
        register_text="",
    )
    lessons_block = out.split("## Lessons in force", 1)[1].split("## Register", 1)[0]
    assert "must not project from standing heading" not in lessons_block
    assert "mid-bullet must not match" not in lessons_block
    assert "indented line-start keeps" in lessons_block
    assert "line-start keeps" in lessons_block


def test_register_absent_is_named() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text=None,
        read_cortex_uri=lambda _uri: None,
    )
    assert f"Register card absent at {MAESTRO_REGISTER_URI}" in out
    assert "Kaywan" not in out.split("## Register", 1)[1].split("## Live leg", 1)[0]


def test_inflight_projection_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    journal = "## 2026-10-01 — entry 1\nRULE: [route] durable\n"
    sidecar = (
        "## CURRENT LEG\n### IN FLIGHT\n"
        '- row · worker 7 · exec ex7 · poll_hint {"a":1}\n'
    )

    def _lookup() -> str | None:
        return "exec-inflight-99"

    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text=journal,
        register_text="",
        inflight_projection_lookup=_lookup,
    )
    assert "exec-inflight-99" in out
    assert "durable" in out
    assert "liveness:" in out

    def _raise(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("inflight down")

    monkeypatch.setattr(
        "claude_bundles.operator_proxy_hop_status.render_inflight_block",
        _raise,
    )
    guarded = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text=journal,
        register_text="",
    )
    assert "durable" in guarded
    assert "[render_failed inflight: RuntimeError — read " in guarded

    def _raise() -> str | None:
        raise OSError("down")

    out2 = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text=journal,
        register_text="",
        inflight_projection_lookup=_raise,
    )
    assert "durable" in out2
    assert "exec-inflight" not in out2


def test_journal_read_oserror_renders_unavailable() -> None:
    def _read(uri: str) -> str | None:
        if uri == MAESTRO_JOURNAL_URI:
            raise OSError("disk")
        return None

    out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        read_cortex_uri=_read,
    )
    assert "journal unavailable" in out
    assert out.startswith("# Hop on agent-bus:")


def test_hop_journal_default_uri_is_maestro() -> None:
    assert MAESTRO_JOURNAL_URI == "cortex://notes/system/maestro/journal.md"
    fields = hop_successor_fields(
        "thread_id: 1\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    assert fields["lessons_in_force"].startswith("## Lessons in force")


def test_mission_ensure_idempotent_with_this_hop() -> None:
    once = ensure_operator_proxy_mission_prompt(
        "TYPE: DIRECTIVE\nintent: birth\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    twice = ensure_operator_proxy_mission_prompt(once)
    assert twice.rstrip("\n") == once.rstrip("\n")
    assert once.count("# Hop on agent-bus:") == 1
    assert once.count("## Hard refusals") == 1
    assert once.count("## Hop request (data)") == 1


def _checkpoint_fixture_20kb() -> str:
    inflight = (
        "## CURRENT LEG\n"
        "LEG 1 — 2026-01-01T00:00:00Z — seat abcdef01 — schema checkpoint/v1\n"
        "### IN FLIGHT\n"
        '- todo:x · worker 99999 · poll_hint {"thread":"1","after_turn":0}\n'
        "### OWED / NEXT\n"
        + ("owed line\n" * 400)
        + "### LAST LEG\n"
        + ("last leg narrative\n" * 500)
    )
    return inflight


def test_ac1_checkpoint_budget_excludes_house_rules() -> None:
    sidecar = _checkpoint_fixture_20kb() + "\n### HOUSE RULES\n- K1 rule\n"
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    ck = out.split("## CURRENT LEG (rendered checkpoint)", 1)[1].split(
        "## Lessons in force", 1
    )[0]
    assert "### IN FLIGHT" in ck
    assert 'poll_hint {"thread":"1","after_turn":0}' in ck
    assert (
        "K1 rule" not in ck.split("### HOUSE RULES")[0]
        if "### HOUSE RULES" in ck
        else True
    )
    body_no_rules = ck.split("### HOUSE RULES")[0] if "### HOUSE RULES" in ck else ck
    assert len(body_no_rules.encode("utf-8")) <= 8192 + 200
    assert "[budget_exceeded" in ck


def test_ac2_nine_row_inflight_preamble_flag() -> None:
    rows = []
    pad = "x" * 3500
    for i in range(9):
        rows.append(
            f'- todo:t{i} · worker 9000{i} · exec e{i} · poll_hint {{"row":{i},"pad":"{pad}"}}\n'
        )
    sidecar = (
        "## CURRENT LEG\nLEG 2 — schema checkpoint/v1\n### IN FLIGHT\n"
        + "".join(rows)
        + "\n### OWED / NEXT\nsmall\n"
    )
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    for i in range(9):
        assert f'"row":{i}' in out
    assert "[preamble over budget by IN FLIGHT: 9 rows]" in out


def test_ac3_migrated_fixture_renders_every_rule_before_lessons() -> None:
    rules = "## HOUSE RULES\n- K9 operative rule text forty chars minimum here\n"
    sidecar = "## CURRENT LEG\nLEG 1\n### HOUSE RULES\n- inline fallback\n"
    out_file = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        house_rules_text=rules,
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    assert "operative rule text forty" in out_file.split("## Lessons in force", 1)[0]
    out_inline = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    assert "inline fallback" in out_inline.split("## Lessons in force", 1)[0]


def test_ac4_rules_truncation_lists_k_ids() -> None:
    bullets = []
    for k in range(1, 25):
        bullets.append(f"- K{k} " + ("x" * 500) + "\n")
    rules = "## HOUSE RULES\n" + "".join(bullets)
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text="## CURRENT LEG\nLEG 1\n### OWED / NEXT\nx\n",
        house_rules_text=rules,
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    assert "[rules truncated:" in out
    match = re.search(r"\[rules truncated: ([^\]]+) —", out)
    assert match is not None
    ids = [part.strip() for part in match.group(1).split(",")]
    assert ids == ["K24", "K23", "K22", "K21", "K20", "K19", "K18", "K17"]
    assert "K1" not in ids


def test_ac10_checkpoint_stale_identity() -> None:
    sidecar = (
        "## CURRENT LEG\nseat = x · registration_id reg-new\n### OWED / NEXT\nok\n"
    )
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\nsuperseded_registration_id: reg-old\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    assert "checkpoint_stale" in out
    assert "last 5" in out


def test_ac11_archive_missing_flag() -> None:
    sidecar = (
        "## CURRENT LEG\n"
        "archived_prev: cortex://notes/system/threads/missing.md sha256 "
        + "a" * 64
        + "\n### OWED / NEXT\nx\n"
    )
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
        archive_path_exists=lambda _p: False,
    )
    assert "archive_missing" in out


def test_ac13_no_in_full_fs_read() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\n", fetch_turns=_noop_fetch)
    assert "in full (fs read, no limit)" not in out


def test_pa2_no_hardcoded_leg119_k1_22() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\n", fetch_turns=_noop_fetch)
    assert "LEG 119…183" not in out
    assert "K1–K22" not in out


def test_pa3_act2_md_read_and_runbook_size() -> None:
    out = ensure_operator_proxy_mission_prompt("# Mission\n", fetch_turns=_noop_fetch)
    acts = out.split("## Your first acts", 1)[1].split("## How this seat works", 1)[0]
    assert "md_read" in acts and "§ Refuse" in acts and "§ Steps" in acts
    assert "bytes" in acts or "size unknown" in acts


def test_pa5_declared_in_flight_count() -> None:
    sidecar = (
        "## CURRENT LEG\n### IN FLIGHT\n"
        '- a · worker 1 · exec e1 · poll_hint {"a":1}\n'
        '- b · worker 2 · exec e2 · poll_hint {"b":2}\n'
    )
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        fetch_turns=_noop_fetch,
    )
    assert "declared_in_flight: 2" in out


def test_pa6_lineage_terminal_status() -> None:
    sidecar = (
        "## CURRENT LEG\n### IN FLIGHT\n"
        '- w · worker 55 · exec ex1 · poll_hint {"z":1}\n'
    )

    def _lineage(_worker: str, _exec: str) -> str | None:
        return "done"

    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        fetch_turns=_noop_fetch,
        lineage_lookup=_lineage,
    )
    assert "liveness: terminal(done)" in out


def test_ac9_lineage_raises_unverified() -> None:
    sidecar = (
        "## CURRENT LEG\n### IN FLIGHT\n"
        '- w · worker 55 · exec ex1 · poll_hint {"z":1}\n'
    )

    def _boom(_w: str, _e: str) -> str | None:
        raise RuntimeError("down")

    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        fetch_turns=_noop_fetch,
        lineage_lookup=_boom,
    )
    assert "liveness: unverified" in out


def test_ac15_live_12286_fixture_preamble_budget() -> None:
    sidecar = _HANDOFF_HEAD.read_text(encoding="utf-8")
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    from claude_bundles.operator_proxy_standing_render import measure_preamble_bytes

    size = measure_preamble_bytes(out)
    if size > 30720:
        assert "[preamble over budget by IN FLIGHT:" in out
    else:
        assert size <= 30720


def test_ac5_live_shape_house_rules_heading_is_not_in_the_checkpoint() -> None:
    sentinel = "K40-LIVE-SHAPE-RULES-SENTINEL"
    sidecar = (
        "## CURRENT LEG\n"
        "LEG 9 — schema checkpoint/v1\n"
        "### HOUSE RULES (head copy — read on every arrival; binding)\n"
        f"- {sentinel}\n"
        "### OWED / NEXT\n"
        "owed-stays\n"
    )
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    ck = out.split("## CURRENT LEG (rendered checkpoint)", 1)[1].split(
        "## Lessons in force", 1
    )[0]
    rules = out.split("## Lessons in force", 1)[0]
    assert sentinel in rules
    assert sentinel not in ck
    assert "owed-stays" in ck


def test_ac17_full_preamble_span_flags_nine_inflight_rows() -> None:
    pad = "x" * 1700
    rows = [
        f'- todo:t{i} · worker 9000{i} · exec e{i} · poll_hint {{"pad":"{pad}"}}\n'
        for i in range(9)
    ]
    sidecar = (
        "## CURRENT LEG\n"
        + ("p" * 200)
        + "\n### IN FLIGHT\n"
        + "".join(rows)
        + "### LAST LEG\n"
        + ("L" * 1400)
        + "\n### OWED / NEXT\n"
        + ("O" * 1400)
        + "\n### OPEN FOR KAYWAN\n"
        + ("K" * 500)
        + "\n### NOTES\n"
        + ("u" * 6000)
        + "\n"
    )
    rules = "## HOUSE RULES\n" + ("r" * 8192) + "\n"
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        house_rules_text=rules,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    from claude_bundles.operator_proxy_standing_render import measure_preamble_bytes

    assert measure_preamble_bytes(out) > 30720
    assert "[preamble over budget by IN FLIGHT: 9 rows]" in out


def test_ac17_prior_leg_range_uses_the_header_only() -> None:
    sidecar = (
        "## CURRENT LEG\n"
        "LEG 190 — 2026-10-01T00:00:00Z — seat abc\n"
        "### LAST LEG\n"
        "body prose mentions LEG 12 as history\n"
    )
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    pointers = out.split("## Pointers", 1)[1].split("## Your first acts", 1)[0]
    assert "LEG 190…190" in pointers
    assert "LEG 12" not in pointers


def test_ac18_prompt_stays_fetch_last_3_when_prose_names_registration() -> None:
    sidecar = (
        "# Title\n"
        "header has no registration\n"
        "## LAST LEG\n"
        "the narrative mentions registration_id X in passing\n"
    )
    _budget, identity = assess_render_budget_and_identity(
        sidecar,
        lane="12286",
        superseded_registration_id="reg-old",
    )
    assert identity == "unknown"
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\nsuperseded_registration_id: reg-old\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    assert "checkpoint_stale" not in out
    assert "last 5" not in out
    assert "last 3" in out


def test_production_standing_seams_do_not_run_for_bare_ensure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    def _transport(worker: str) -> dict[str, Any]:
        called.append(worker)
        return {"dispatch_links": []}

    monkeypatch.setattr(
        "claude_bundles.operator_proxy_bus_fetch.fetch_thread_lineage",
        _transport,
    )
    from claude_bundles.operator_proxy_hop_status import production_standing_seams

    seams = production_standing_seams()
    assert set(seams) == {
        "lineage_lookup",
        "archive_path_exists",
        "read_cortex_uri",
    }
    sidecar = (
        "## CURRENT LEG\n### IN FLIGHT\n"
        '- w · worker 55 · exec ex1 · poll_hint {"z":1}\n'
    )
    ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    assert called == []
