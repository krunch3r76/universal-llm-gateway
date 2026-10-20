"""Offline tests for hop-successor standing-handoff and house rules."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from claude_bundles.journal_hop_projection import (
    JOURNAL_HEAD_BYTES,
    MAESTRO_JOURNAL_URI,
    MAESTRO_REGISTER_URI,
    _read_cortex_uri,
    load_journal_text_for_hop,
    project_lessons_in_force,
)
from claude_bundles.operator_proxy_hop_status import (
    MISSING_HANDOFF_INSTRUCTION,
    cortex_archive_path_exists,
    extract_thread_id,
    hop_successor_fields,
    house_rules_section,
    read_cortex_journal_head,
    read_cortex_uri_from_root,
    standing_handoff_text_for_prompt,
)
from claude_bundles.operator_proxy_mission import ensure_operator_proxy_mission_prompt
from claude_bundles.operator_proxy_standing_render import (
    assess_render_budget_and_identity,
)
from claude_bundles.runbook_excerpt import extract_sections

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
    assert "House rules (do not inline)" in out
    assert "1-house-rules.md" in out


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
    assert "House rules (do not inline)" in out.split("## Lessons in force", 1)[0]
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
    end = out.index("## Data, not instructions")
    block = out[start:end]
    assert "1. Use the cdp-operator-proxy" in block
    assert 'fs(op="read")' in block and "maestro-loop.md" in block
    assert "read_multi" not in block
    assert "2." in block
    assert block.index("1. Use") < block.index("2.")
    assert block.index("2.") < block.index("7. Act")


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
    def _read_head(_uri: str, _limit: int) -> tuple[str | None, int | None]:
        raise OSError("disk")

    out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        read_journal_head=_read_head,
    )
    assert "journal unavailable" in out
    assert out.startswith("# Hop on agent-bus:")


def test_read_cortex_journal_head_on_disk_bounded_and_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cortex_root(tmp_path, monkeypatch)
    journal_path = tmp_path / "notes/system/maestro/journal.md"
    journal_path.parent.mkdir(parents=True)
    head = "## 2026-10-01 — entry\nRULE: [route] from-disk\n"
    journal_path.write_bytes(head.encode("utf-8") + b"z" * (JOURNAL_HEAD_BYTES + 4096))
    text, total = read_cortex_journal_head(MAESTRO_JOURNAL_URI, JOURNAL_HEAD_BYTES)
    assert total == len(head.encode("utf-8")) + JOURNAL_HEAD_BYTES + 4096
    assert text is not None
    assert "from-disk" in text
    assert len(text.encode("utf-8")) <= JOURNAL_HEAD_BYTES


def test_read_cortex_journal_head_unreadable_renders_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cortex_root(tmp_path, monkeypatch)
    journal_path = tmp_path / "notes/system/maestro/journal.md"
    journal_path.parent.mkdir(parents=True)
    journal_path.write_text("## 2026-10-01 — entry\nRULE: [route] hidden\n")
    os.chmod(journal_path, 0o000)
    try:
        out = ensure_operator_proxy_mission_prompt(
            "# Mission\n",
            roster_text="",
            fetch_turns=_noop_fetch,
            register_text="",
        )
    finally:
        os.chmod(journal_path, 0o644)
    assert "journal unavailable" in out
    assert (
        "(none)"
        not in out.split("## Lessons in force", 1)[1].split("## Register", 1)[0]
    )


def test_journal_head_trim_drops_severed_rule_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cortex_root(tmp_path, monkeypatch)
    journal_path = tmp_path / "notes/system/maestro/journal.md"
    journal_path.parent.mkdir(parents=True)
    body = b"## 2026-10-01 -- entry\nRULE: [route] kept_rule\n"
    severed = b"RULE: [route] severed_must_not_project"
    while len(body) < JOURNAL_HEAD_BYTES - len(severed) + 5:
        body += b"fill\n"
    assert body.endswith(b"\n")
    assert len(body) < JOURNAL_HEAD_BYTES < len(body) + len(severed)
    journal_path.write_bytes(body + severed + b"\n" + b"w" * 8192)
    out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        register_text="",
    )
    lessons = out.split("## Lessons in force", 1)[1].split("## Register", 1)[0]
    assert "kept_rule" in lessons
    assert "severed_must_not_project" not in lessons


def test_load_journal_head_read_bounded_at_64kb() -> None:
    seen_limits: list[int] = []
    five_mb = 5 * 1024 * 1024

    def _read_head(_uri: str, limit: int) -> tuple[str | None, int | None]:
        seen_limits.append(limit)
        head = "## 2026-10-01 — entry\nRULE: [route] head rule\n"
        return head, five_mb

    text, err, total = load_journal_text_for_hop(read_journal_head=_read_head)
    assert err is None
    assert total == five_mb
    assert "head rule" in (text or "")
    assert seen_limits == [JOURNAL_HEAD_BYTES]


def test_journal_head_projection_matches_full_under_budget() -> None:
    journal = _journal_fixture_seven_plus_fleet()
    direct = project_lessons_in_force(journal, role="maestro")
    via_hop = hop_successor_fields(
        "thread_id: 1\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text=journal,
        register_text="",
    )["lessons_in_force"]
    assert via_hop == direct


def test_journal_head_over_budget_renders_flag() -> None:
    rules = "".join(
        f"## 2026-10-{index:02d} — entry {index}\nRULE: [route] rule {index}\n"
        for index in range(7, 0, -1)
    )
    pad = "x" * (JOURNAL_HEAD_BYTES - len(rules.encode("utf-8")) + 1024)
    journal = (
        f"archived_prev: cortex://notes/system/threads/prev.md sha256 {'a' * 64}\n"
        f"{rules}\n{pad}\n"
    )

    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text=journal,
        register_text="",
        archive_path_exists=lambda _p: True,
    )
    assert "[journal_over_budget" in out
    assert "65536" in out
    assert "rotation owed at next hop" in out
    lessons = out.split("## Lessons in force", 1)[1].split("## Register", 1)[0]
    assert lessons.count("RULE: [route]") == 5
    assert "rule 7" in lessons
    assert "rule 3" in lessons
    assert "rule 2" not in lessons


def _exact_utf8(text: str, total: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) > total:
        raise AssertionError(f"prefix {len(encoded)} exceeds {total}")
    pad = total - len(encoded)
    if pad == 0:
        return text
    return text + ("x" * (pad - 1)) + "\n"


def test_housekeeping_journal_over_budget_from_head_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cortex_root(tmp_path, monkeypatch)
    journal_path = tmp_path / "notes/system/maestro/journal.md"
    journal_path.parent.mkdir(parents=True)
    entry = "## 2026-10-01 — entry\nRULE: [route] r\n"
    journal_path.write_bytes(_exact_utf8(entry, 70000).encode("utf-8"))
    out = ensure_operator_proxy_mission_prompt(
        "hop_kind: housekeeping\nthread_id: 12286\n",
        standing_handoff_text="## CURRENT LEG\n### OWED / NEXT\nx\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        register_text="",
    )
    assert "[journal_over_budget 70000/65536 — rotation owed at next hop]" in out
    assert "| 70000/65536 |" in out

    journal_path.write_bytes(_exact_utf8(entry, 1000).encode("utf-8"))
    under = ensure_operator_proxy_mission_prompt(
        "hop_kind: housekeeping\nthread_id: 12286\n",
        standing_handoff_text="## CURRENT LEG\n### OWED / NEXT\nx\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        register_text="",
    )
    assert "[journal_over_budget" not in under
    assert "| 1000/65536 |" in under


def test_runbook_cost_is_refuse_plus_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cortex_root(tmp_path, monkeypatch)
    runbook = tmp_path / "notes/runbooks/maestro-loop.md"
    runbook.parent.mkdir(parents=True)
    body = (
        "## Refuse\n"
        + ("r\n" * 200)
        + "## Steps\n"
        + ("s\n" * 200)
        + "## Appendix\n"
        + ("a" * 40000)
        + "\n"
    )
    runbook.write_text(body, encoding="utf-8")
    n = len(extract_sections(body, ("Refuse", "Steps")).encode("utf-8"))
    whole = len(body.encode("utf-8"))
    assert n < 32768 < whole
    housekeeping = ensure_operator_proxy_mission_prompt(
        "hop_kind: housekeeping\nthread_id: 12286\n",
        standing_handoff_text="## CURRENT LEG\n### OWED / NEXT\nx\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        register_text="",
    )
    mission = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text="## CURRENT LEG\n### OWED / NEXT\nx\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        register_text="",
    )
    assert f"| {n}/32768 |" in housekeeping
    assert 'section="Trigger"' in mission
    assert 'fs(op="read")' in mission
    assert str(whole) not in housekeeping
    assert str(whole) not in mission

    over = "## Refuse\n" + "r\n" + "## Steps\n" + ("s" * 40000) + "\n"
    runbook.write_text(over, encoding="utf-8")
    over_n = len(extract_sections(over, ("Refuse", "Steps")).encode("utf-8"))
    assert over_n > 32768
    over_out = ensure_operator_proxy_mission_prompt(
        "hop_kind: housekeeping\nthread_id: 12286\n",
        standing_handoff_text="## CURRENT LEG\n### OWED / NEXT\nx\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        register_text="",
    )
    cell = f"| {over_n}/32768 |"
    assert cell in over_out
    numerator = cell.split("|")[1].strip().split("/")[0]
    assert int(numerator) > 32768


def test_journal_archived_prev_missing_flags_archive_missing() -> None:
    journal = (
        f"archived_prev: cortex://notes/system/threads/missing-prev.md sha256 {'b' * 64}\n"
        "## 2026-10-01 — entry\nRULE: [route] ok\n"
    )
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text=journal,
        register_text="",
        archive_path_exists=lambda _p: False,
    )
    assert "archive_missing: journal" in out
    assert "ok" in out


def test_journal_archive_path_oserror_does_not_abort_hop() -> None:
    journal = (
        f"archived_prev: cortex://notes/system/threads/unreadable.md sha256 {'c' * 64}\n"
        "## 2026-10-01 — entry\nRULE: [route] ok\n"
    )

    def _exists(_path: str) -> bool:
        raise OSError("perm")

    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text=journal,
        register_text="",
        archive_path_exists=_exists,
    )
    assert "[render_failed archive_path: OSError" in out
    assert "ok" in out
    assert out.startswith("# Hop on agent-bus:")


def test_hop_successor_fields_parses_hop_kind_and_body_metadata() -> None:
    prompt = (
        "TYPE: CONTINUITY_HANDOFF\n"
        "thread_id: 12286\n"
        "hop_kind: housekeeping\n"
        "standing_handoff_budget: exceeded\n"
        "standing_handoff_identity: stale\n"
        "inflight_rows: 3\n"
    )
    sidecar = "## CURRENT LEG\n### OWED / NEXT\nx\n"
    fields = hop_successor_fields(
        prompt,
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    assert fields["hop_kind"] == "housekeeping"
    assert fields["budgets_table"].startswith("## Budgets")


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
    inflight = out.split("## In flight (from the checkpoint", 1)[1].split(
        "## Live leg", 1
    )[0]
    assert 'poll_hint {"thread":"1","after_turn":0}' in inflight
    assert "### IN FLIGHT" not in ck
    assert (
        "K1 rule" not in ck.split("### HOUSE RULES")[0]
        if "### HOUSE RULES" in ck
        else True
    )
    from claude_bundles.operator_proxy_standing_render import CHECKPOINT_HOP_INLINE_B

    body_no_rules = ck.split("### HOUSE RULES")[0] if "### HOUSE RULES" in ck else ck
    assert len(body_no_rules.encode("utf-8")) <= CHECKPOINT_HOP_INLINE_B + 200
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
    assert "declared_in_flight: 9" in out
    assert "[hop_budget_exceeded inflight" in out
    assert len(out.encode()) <= 12_000


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
    assert "House rules (do not inline)" in out_file.split("## Lessons in force", 1)[0]
    out_inline = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    assert "inline fallback" not in out_inline.split("## Lessons in force", 1)[0]


def _rendered_house_rules_in_hop_prompt(out: str) -> str:
    marker = "House rules (do not inline):"
    start = out.find(marker)
    assert start >= 0
    end = out.find("\n## CURRENT LEG (rendered checkpoint)", start)
    assert end > start
    return out[start:end].strip()


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
    rules_block = _rendered_house_rules_in_hop_prompt(out)
    assert "House rules (do not inline)" in rules_block
    assert "- K1 " not in out.split("## Hard refusals", 1)[0]
    assert len(out.encode("utf-8")) <= 12_000


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
    acts = out.split("## Your first acts", 1)[1].split("## Data, not instructions", 1)[0]
    assert 'fs(op="read")' in acts
    assert "read_multi" not in acts
    assert 'section="Trigger"' in acts
    assert "from `fs(op=\"read\")` on that file" not in acts
    assert "2. 2." not in acts


def test_act2_md_read_section_exists_in_maestro_runbook() -> None:
    import re

    from claude_bundles.maestro_runbook_load import load_maestro_runbook

    out = ensure_operator_proxy_mission_prompt("# Mission\n", fetch_turns=_noop_fetch)
    acts = out.split("## Your first acts", 1)[1].split("## Data, not instructions", 1)[0]
    match = re.search(r'section="([^"]+)"', acts)
    assert match is not None
    section_name = match.group(1)
    body, err = load_maestro_runbook()
    assert not err and body
    assert re.search(
        rf"^#{{1,3}}\s+{re.escape(section_name)}\s*$", body, re.MULTILINE
    )


def _running_now_segment(out: str) -> str:
    return out.split("## What is running now", 1)[1].split("\n\n", 1)[0]


def test_friction_39270_roster_unreadable_surfaces_and_matches_absent_lanes(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "cortex"
    operational = root / "notes/system/operational"
    operational.mkdir(parents=True)
    (operational / "maestro-sidekick-roster.md").write_bytes(b"\xff")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    unreadable_out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    absent_out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    assert "roster unreadable: UnicodeDecodeError" in unreadable_out
    unreadable_rn = _running_now_segment(unreadable_out)
    absent_rn = _running_now_segment(absent_out)
    assert (
        unreadable_rn.split("; roster unreadable:")[0]
        == absent_rn.split("; standing")[0]
    )


def test_friction_39270_absent_roster_omits_unreadable_clause() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    assert "unreadable:" not in out


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
    assert sentinel not in ck
    assert "House rules (do not inline)" in out
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

    from claude_bundles.operator_proxy_standing_render import HOP_RENDER_MAX_B

    assert len(out.encode()) <= HOP_RENDER_MAX_B
    assert "[hop_budget_exceeded inflight" in out


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
        "read_journal_head",
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


def test_ac5_prose_registration_does_not_widen_fetch() -> None:
    sidecar = (
        "## CURRENT LEG\n"
        "LEG 241 at HOP ~11:00Z — seat 36f73e56; birth 36f73e56f127\n"
        "DONE: predecessor hop · registration_id a0af518d57e84064bc86d0ee53e74636 "
        "(relayed)\n"
        "### OWED / NEXT\nok\n"
    )
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\nsuperseded_registration_id: reg-old\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
    )
    assert "last 3" in out
    assert "last 5" not in out
    assert "checkpoint_stale" not in out


def test_ac8_journal_projection_raise_replaces_only_those_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> dict[str, str]:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "claude_bundles.operator_proxy_hop_status.hop_journal_projection_fields",
        _boom,
    )
    sidecar = (
        "## HOUSE RULES\n- K1 rules-still-here\n\n"
        "## CURRENT LEG\n### OWED / NEXT\ncheckpoint-x\n"
    )
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=lambda _lane: [],
        journal_text="",
        register_text="",
    )
    assert (
        "[render_failed lessons_in_force: RuntimeError — read "
        "cortex://notes/system/maestro/journal.md]"
    ) in out
    assert "[render_failed register_card: RuntimeError" in out
    assert "House rules (do not inline)" in out
    assert "checkpoint-x" in out
    assert "## Hard refusals" in out


_ABSENT_HANDOFF = "standing handoff file absent"
_ABSENT_REGISTER = f"Register card absent at {MAESTRO_REGISTER_URI}"
_SHA = "a" * 64


def _cortex_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))


def test_standing_handoff_absent_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cortex_root(tmp_path, monkeypatch)
    loaded = standing_handoff_text_for_prompt("thread_id: 42\n")
    assert loaded is None
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 42\n",
        standing_handoff_text=loaded,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    assert _ABSENT_HANDOFF in out


def test_standing_handoff_oserror_is_render_failed() -> None:
    def _read(_thread_id: str) -> str | None:
        raise OSError("disk")

    loaded = standing_handoff_text_for_prompt("thread_id: 42\n", read_path=_read)
    assert loaded == (
        "[render_failed standing_handoff: OSError — read "
        "cortex://notes/system/threads/42-standing-handoff.md]"
    )
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 42\n",
        standing_handoff_text=loaded,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    assert loaded in out
    assert _ABSENT_HANDOFF not in out
    assert "is absent" not in out


def test_standing_handoff_non_utf8_is_render_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cortex_root(tmp_path, monkeypatch)
    path = tmp_path / "notes/system/threads/42-standing-handoff.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff")
    loaded = standing_handoff_text_for_prompt("thread_id: 42\n")
    assert loaded is not None
    assert "UnicodeDecodeError" in loaded
    assert loaded.startswith("[render_failed standing_handoff:")
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 42\n",
        standing_handoff_text=loaded,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
    )
    assert loaded in out
    assert _ABSENT_HANDOFF not in out
    assert "## Hard refusals" in out


def test_archive_path_absent_is_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cortex_root(tmp_path, monkeypatch)
    assert (
        cortex_archive_path_exists("cortex://notes/system/threads/missing.md") is False
    )


def test_archive_path_oserror_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(self: Path) -> bool:
        raise OSError("stat")

    monkeypatch.setattr(Path, "is_file", _boom)
    with pytest.raises(OSError, match="stat"):
        cortex_archive_path_exists("cortex://notes/system/threads/x.md")
    monkeypatch.undo()
    sidecar = (
        "## CURRENT LEG\n"
        "archived_prev: cortex://notes/system/threads/x.md sha256 "
        + _SHA
        + "\n### OWED / NEXT\nx\n"
    )

    def _exists(_path: str) -> bool:
        raise OSError("stat")

    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 42\n",
        standing_handoff_text=sidecar,
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text="",
        archive_path_exists=_exists,
    )
    assert (
        "[render_failed archive_path: OSError — read cortex://notes/system/threads/x.md]"
        in out
    )
    assert "archive_missing" not in out


def test_archive_path_non_utf8_still_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cortex_root(tmp_path, monkeypatch)
    path = tmp_path / "notes/system/threads/bin.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff")
    assert cortex_archive_path_exists("cortex://notes/system/threads/bin.md") is True


def test_read_cortex_uri_absent_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cortex_root(tmp_path, monkeypatch)
    assert (
        read_cortex_uri_from_root("cortex://notes/system/maestro/register.md") is None
    )
    assert _read_cortex_uri(MAESTRO_REGISTER_URI) is None
    out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text=None,
        read_cortex_uri=read_cortex_uri_from_root,
    )
    assert _ABSENT_REGISTER in out


def test_read_cortex_uri_oserror_is_render_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cortex_root(tmp_path, monkeypatch)
    path = tmp_path / "notes/system/maestro/register.md"
    path.parent.mkdir(parents=True)
    path.write_text("card\n", encoding="utf-8")

    def _boom(self: Path, *args: object, **kwargs: object) -> str:
        raise OSError("read")

    monkeypatch.setattr(Path, "read_text", _boom)
    with pytest.raises(OSError, match="read"):
        read_cortex_uri_from_root(MAESTRO_REGISTER_URI)
    out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text=None,
        read_cortex_uri=read_cortex_uri_from_root,
    )
    assert "[render_failed register_card: OSError — read " in out
    assert _ABSENT_REGISTER not in out


def test_read_cortex_uri_non_utf8_is_render_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cortex_root(tmp_path, monkeypatch)
    path = tmp_path / "notes/system/maestro/register.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        read_cortex_uri_from_root(MAESTRO_REGISTER_URI)
    with pytest.raises(UnicodeDecodeError):
        _read_cortex_uri(MAESTRO_REGISTER_URI)
    out = ensure_operator_proxy_mission_prompt(
        "# Mission\n",
        roster_text="",
        fetch_turns=_noop_fetch,
        journal_text="",
        register_text=None,
    )
    assert (
        "[render_failed register_card: UnicodeDecodeError — read "
        f"{MAESTRO_REGISTER_URI}]"
    ) in out
    assert _ABSENT_REGISTER not in out
    assert "## Hard refusals" in out


def test_plain_inflight_line_enables_l23_filter_not_prose_mention() -> None:
    from claude_bundles.operator_proxy_live_leg import LaneFoldState
    from claude_bundles.operator_proxy_standing_render import (
        current_leg_has_inflight_section,
        extract_inflight_work_keys,
        inflight_section_from_sidecar,
    )

    _leg_tail = (
        "OPEN: 15851#2298 reds unowned; a:39707 needs owner on bus\n"
        "CARRIED (not re-read): prior leg fold keys only\n"
        "Carried: lowercase label still terminates\n"
        "NEXT: (1) ok step\n"
        "Traps: none listed\n"
        "FRICTION: none blocking\n"
    )

    prose = (
        "## CURRENT LEG\n"
        "LEG 9 — 12286#2086 — narrative mentions IN FLIGHT items elsewhere\n"
        "### OWED / NEXT\nok\n"
    )
    assert not current_leg_has_inflight_section(prose.split("## CURRENT LEG", 1)[1])

    sidecar_empty = (
        "## CURRENT LEG\n"
        "LEG 11 — 12286#5000 — seat abc — schema checkpoint/v1\n"
        "IN FLIGHT:\n"
        + _leg_tail
    )
    sidecar_named = (
        "## CURRENT LEG\n"
        "LEG 11 — 12286#5000 — seat abc — schema checkpoint/v1\n"
        "IN FLIGHT:\n"
        "- (16416, decision:x) · worker 15851 · exec e1 · poll_hint {}\n"
        "- Opening note: prose must not terminate plain IN FLIGHT\n"
        "- Nextcloud: path mention must not terminate\n"
        + _leg_tail
    )
    assert current_leg_has_inflight_section(sidecar_empty.split("## CURRENT LEG", 1)[1])
    inflight_named = inflight_section_from_sidecar(sidecar_named)
    assert "OPEN:" not in inflight_named
    assert "CARRIED" not in inflight_named
    assert "Carried:" not in inflight_named
    keys_named = extract_inflight_work_keys(inflight_named)
    assert "decision:x" in keys_named
    assert "a:39707" not in keys_named

    stale_keys = [
        "15851:1560:REQUEST|freeform|decision:anthropic-api-credits-use-A — stargate path check: does pid 1555855 (43013d190) load any path changed in 43013d190..b23846d77? Read-only, FACT back",
        "15851:1577:REQUEST|freeform|decision:anthropic-api-credits-use-C1 — did RULING #1570 reach cc1? Name cc1's worker thread and work_key for the harness fix leg (read-only)",
        "15851:1613:PLAN|friction:39550 r2 sha f94b1b05 — VERDICT PASS_WITH_NITS (2cb8d764fd86-542620b1)",
        "15851:1631:PLAN|friction:39330|cc of #1630 (lane 17905)",
        "15851:1798:PICKUP|friction:39610 #1793 items 1-2 dispatched; #1790 relays out; restarts per Kaywan (re #1790, #1793, #1794)",
        "15851:1853:PICKUP|friction:39550 house 16416 (cursor) under f94b1b05; 39516 step-0 probe + 39330 c9d6e983 folded (re #1849)",
        "15851:1854:PICKUP|friction:39614 residue investigate routed to house 16416 (read-only leg first) (re #1852)",
        "15851:1983:PLAN|todo:cursor-sdk-token-efficiency-lean-a-d|Shape A PLAN ready for K43 xhigh review via gc1 (sha 7d367208; queued behind #1902 / 39668 / 39638)",
        "15851:1998:PLAN|a:39687 G1+G2 implement proposal 2/2: tests and gates (re RULING #1996)",
        "15851:2037:REQUEST|a:39707 needs owner; fable-5-1 consult on floor enforcement first",
        "15851:2080:REQUEST|freeform|friction:39707: relay one question to Kaywan; the 2026-10-07 composer retirement exists only as a runbook changelog line",
        "15851:2081:PLAN|todo:conductor-admit-pipeline-options-shape-a r-next^2",
        "15851:2083:REQUEST|freeform|friction:39668: child 4 (child 3 refused step1_mcp_not_exception_b on operator streams); route a:39712; route K43s on #2081 and #2082",
        "15851:2091:REQUEST|freeform|friction:39710 range forward fixes (K46 18302 REQUEST_CHANGES)",
        "15851:2281:REQUEST|freeform|friction:30054 — co-sign or rule: K43 bar amendment per fork D (VERDICT line binds; envelope capture class informational); ACK 16416#863",
        "15851:2300:REQUEST|freeform|todo:conductor-admit-pipeline-options-shape-a — stargate sync_restart on G1-G3 to load bb75af8b",
    ]
    fold_state = {
        "15851": LaneFoldState(watermark=3000, open_keys=stale_keys),
        "15722": LaneFoldState(watermark=90, open_keys=[]),
    }

    def _fetch(lane: str) -> list[dict[str, Any]]:
        if lane == "15722":
            return [{"turn_number": 150, "subject": "PLAN|todo:peer-open"}]
        if lane == "15851":
            return [{"turn_number": 4000, "subject": "PICKUP|decision:x"}]
        return []

    roster = (
        "| N | Display name | Lane | Mailbox | State |\n"
        "| 1 | Sidekick | 15722 `sidekick` | `x` | live |\n"
        "| 2 | GC | 15851 `gc` | `y` | live |\n"
    )
    fields_empty = hop_successor_fields(
        "thread_id: 12286\nhop_kind: mission\n",
        standing_handoff_text=sidecar_empty,
        roster_text=roster,
        fetch_turns=_fetch,
        fold_state_load=lambda: dict(fold_state),
        fold_state_save=lambda _s: None,
    )
    live_empty = fields_empty["live_leg"]
    assert "todo:peer-open" in live_empty
    assert "a:39707" not in live_empty
    assert "fable-5-1 consult on floor enforcement" not in live_empty
    assert "conductor-admit-pipeline-options-shape-a — stargate" not in live_empty

    fields_named = hop_successor_fields(
        "thread_id: 12286\nhop_kind: mission\n",
        standing_handoff_text=sidecar_named,
        roster_text=roster,
        fetch_turns=_fetch,
        fold_state_load=lambda: dict(fold_state),
        fold_state_save=lambda _s: None,
    )
    live_named = fields_named["live_leg"]
    assert "decision:x" in live_named
    assert "todo:peer-open" not in live_named
    assert "a:39707" not in live_named
    assert "needs owner" not in live_named
