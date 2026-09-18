"""Tests for the WAKE doorbell renderer."""

from __future__ import annotations

from pathlib import Path

import pytest

import re

from bus_watch.doorbell import (
    DOORBELL_CAP,
    SUCCESSOR_WAKE_CAP,
    basis_floor_bytes,
    ensure_doorbell_file,
    parse_doorbell_file,
    render_address,
    render_doorbell,
    render_successor_wake,
    successor_wake_unshed_byte_length,
)
from bus_watch.doorbell_skills import (
    liaison_protocol_sot_uri,
    seat_dispatch_surface,
    seat_doorbell_surface,
)

_DEFAULT_ARGS = ("10479", "liaison-autonomous-night")
_DEFAULT_KW = {"ring": "10532", "seat": "cursor-sdk"}
_LIVE_10479_KW = {
    "ring": "10532",
    "seat": "web-anthropic",
    "include_commission": True,
}


def _default_render() -> str:
    return render_doorbell(*_DEFAULT_ARGS, **_DEFAULT_KW)


def _live_10479_render() -> str:
    return render_doorbell(*_DEFAULT_ARGS, **_LIVE_10479_KW)


@pytest.mark.offline
def test_parse_doorbell_file_recovers_root_slug_ring() -> None:
    text = _live_10479_render()
    parsed = parse_doorbell_file(text)
    assert parsed.root == "10479"
    assert parsed.slug == "liaison-autonomous-night"
    assert parsed.ring == "10532"


@pytest.mark.offline
def test_parse_doorbell_file_ring_none_when_echo_is_root() -> None:
    text = render_doorbell("10479", "liaison-wake")
    parsed = parse_doorbell_file(text)
    assert parsed.ring is None
    assert "agent-bus:10479 (echo)" in text


@pytest.mark.offline
def test_ensure_doorbell_file_refreshes_stale_content(tmp_path: Path) -> None:
    path = tmp_path / "10479-liaison-wake-doorbell.md"
    fresh = _live_10479_render()
    stale = fresh.replace(
        "line-start `scope:` + `files_expected:` + `vision:`",
        "legacy commission hint without admission tokens",
    )
    path.write_text(stale, encoding="utf-8")

    result = ensure_doorbell_file(path, root="10479")
    assert result.wrote is True
    assert path.read_text(encoding="utf-8") == fresh

    mtime_before = path.stat().st_mtime_ns
    second = ensure_doorbell_file(path, root="10479")
    assert second.wrote is False
    assert path.stat().st_mtime_ns == mtime_before


@pytest.mark.offline
def test_ensure_doorbell_file_defaults_preserve_existing_slug_and_ring(
    tmp_path: Path,
) -> None:
    path = tmp_path / "10479-liaison-wake-doorbell.md"
    text = _live_10479_render()
    path.write_text(text, encoding="utf-8")

    result = ensure_doorbell_file(path, root="10479")
    assert result.wrote is False
    assert result.slug == "liaison-autonomous-night"
    assert result.ring == "10532"
    unchanged = path.read_text(encoding="utf-8")
    assert "liaison-autonomous-night" in unchanged
    assert "agent-bus:10532 (echo)" in unchanged
    assert "liaison-wake" not in unchanged.splitlines()[0]


@pytest.mark.offline
def test_ensure_doorbell_file_explicit_slug_override_rewrites(
    tmp_path: Path,
) -> None:
    path = tmp_path / "10479-liaison-wake-doorbell.md"
    path.write_text(_live_10479_render(), encoding="utf-8")
    expected = render_doorbell("10479", "liaison-wake", ring="10532")

    result = ensure_doorbell_file(
        path,
        root="10479",
        slug="liaison-wake",
        slug_explicit=True,
        ring="10532",
        ring_explicit=True,
    )
    assert result.wrote is True
    assert path.read_text(encoding="utf-8") == expected


@pytest.mark.offline
def test_ensure_doorbell_file_idempotent_on_live_10479_artifact(
    tmp_path: Path,
) -> None:
    path = tmp_path / "10479-liaison-wake-doorbell.md"
    text = _live_10479_render()
    path.write_text(text, encoding="utf-8")

    result = ensure_doorbell_file(path, root="10479")
    assert result.wrote is False
    assert path.read_bytes() == text.encode("utf-8")


@pytest.mark.offline
def test_ensure_doorbell_file_creates_new_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / "99999-liaison-wake-doorbell.md"
    expected = render_doorbell("99999", "liaison-wake")

    result = ensure_doorbell_file(path, root="99999")
    assert result.wrote is True
    assert path.read_text(encoding="utf-8") == expected


@pytest.mark.offline
def test_default_render_contains_required_fragments() -> None:
    text = _default_render()
    assert "agent_bus_read(fetch, thread=10479, last=10, compact=true)" in text
    assert "agent-bus:10532 (echo)" in text
    assert 'subject="ORIENTED 10479"' in text
    assert "Use the liaison skill." in text
    assert "Use the reasoning-posture skill." in text
    assert "scheduled task liaison-wake-10479" in text
    assert "agent-bus:10532" in text
    assert "agent-bus:10479 (echo)" not in text
    assert "parent_thread=10479" in text
    assert "lane_role=sub_mission" in text
    assert "new_slug=r15-wake-<slug>" in text
    assert "cursor_request(thread=10479" not in text


@pytest.mark.offline
def test_web_anthropic_doorbell_classifies_as_life_not_ide() -> None:
    """AC1 — life seat maps to ``life`` surface; classifier does not fold to IDE."""
    assert seat_dispatch_surface("web-anthropic") == "life"
    assert seat_doorbell_surface("web-anthropic") == "life"
    assert seat_doorbell_surface("web-anthropic") != "ide"


@pytest.mark.offline
def test_web_anthropic_doorbell_carries_resolvable_liaison_not_use_line() -> None:
    """AC2 / falsifier — no Customize self-fetch; liaison SOT is addressable."""
    text = render_doorbell(
        "10479",
        "liaison-autonomous-night",
        ring="10532",
        seat="web-anthropic",
        include_commission=True,
    )
    assert not re.search(r"^Use the liaison skill\.$", text, re.MULTILINE)
    assert render_address(liaison_protocol_sot_uri()) in text
    assert "Use the reasoning-posture skill." in text


@pytest.mark.offline
def test_ring_none_echoes_root() -> None:
    text = render_doorbell("10479", "liaison-autonomous-night")
    assert "thread=10479" in text
    assert 'subject="ORIENTED 10479"' in text
    assert "agent-bus:10479 (echo)" in text
    assert "10532" not in text


@pytest.mark.offline
def test_determinism_and_headroom() -> None:
    first = _default_render()
    second = _default_render()
    assert first == second
    encoded_len = len(first.encode("utf-8"))
    assert encoded_len <= DOORBELL_CAP - 100


@pytest.mark.offline
def test_forbidden_content_absent() -> None:
    text = _default_render()
    lower = text.lower()
    assert "you are" not in lower
    assert "now:" not in lower
    assert not any(line.startswith("NOW") for line in text.splitlines())


@pytest.mark.offline
def test_skills_tuple_controls_skill_lines() -> None:
    one_skill = render_doorbell(*_DEFAULT_ARGS, ring="10532", skills=("liaison",))
    skill_lines = [ln for ln in one_skill.splitlines() if ln.startswith("Use the ")]
    assert len(skill_lines) == 1
    assert skill_lines[0] == "Use the liaison skill."

    no_skills = render_doorbell(*_DEFAULT_ARGS, ring="10532", skills=())
    assert not any(ln.startswith("Use the ") for ln in no_skills.splitlines())


@pytest.mark.offline
def test_extra_addresses_render_md_read() -> None:
    extras = (
        "cortex://notes/system/threads/10479-operator-guide.md",
        "cortex://notes/system/threads/10479-charter-scoreboard.md#Loop",
    )
    text = render_doorbell(
        *_DEFAULT_ARGS, ring="10532", extra_addresses=extras, cap=8192
    )
    assert (
        "fs(op=md_read, path=cortex://notes/system/threads/10479-operator-guide.md)"
        in text
    )
    assert (
        "fs(op=md_read, path=cortex://notes/system/threads/10479-charter-scoreboard.md, section=Loop)"
        in text
    )


@pytest.mark.offline
def test_seating_render_sheds_placeholders_not_addresses() -> None:
    """One planted address must render, not force a hand-paste (10479 CDP seating)."""
    extras = ("cortex://notes/system/threads/10479-charter-scoreboard.md#Loop",)
    text = render_doorbell(
        "10479",
        "claude-ai-navigator-seat",
        ring="10532",
        seat="cursor-sdk",
        extra_addresses=extras,
        fired_by="cdp generate on agent-bus:11165, not a scheduled task",
        cap=1024,
    )
    assert len(text.encode("utf-8")) <= 1024
    assert "section=Loop" in text
    assert "Use the liaison skill." in text
    assert "Use the reasoning-posture skill." in text
    assert "digest: <DIGEST subject>" in text
    assert "chat: <url>" in text
    assert "tools: <count>" not in text


@pytest.mark.offline
def test_shedding_leaves_the_default_render_untouched() -> None:
    text = _default_render()
    assert "tools: <count>" in text
    assert "objective: <root.last_subject>" in text
    assert "if attention mint; quiet echo;" in text


@pytest.mark.offline
def test_cap_enforced_and_overridable() -> None:
    long_extras = tuple(
        f"cortex://notes/system/threads/file-{idx}.md" for idx in range(40)
    )
    with pytest.raises(ValueError, match="1400"):
        render_doorbell(*_DEFAULT_ARGS, ring="10532", extra_addresses=long_extras)
    render_doorbell(
        *_DEFAULT_ARGS,
        ring="10532",
        extra_addresses=long_extras,
        cap=8192,
    )


@pytest.mark.offline
def test_line_count_and_trailing_newline() -> None:
    for skills in (("liaison", "reasoning-posture"), ("liaison",), ()):
        text = render_doorbell(*_DEFAULT_ARGS, ring="10532", skills=skills)
        assert text.endswith("\n")
        assert text.count("\n") == 8 + len(skills)
        assert len(text.splitlines()) == 8 + len(skills)


@pytest.mark.offline
def test_render_address_public() -> None:
    assert render_address("a.md#S") == "fs(op=md_read, path=a.md, section=S)"
    assert render_address("a.md") == "fs(op=md_read, path=a.md)"


@pytest.mark.offline
def test_fired_by_overrides_scheduled_frame() -> None:
    text = render_doorbell(
        *_DEFAULT_ARGS,
        ring="10532",
        fired_by="cdp generate on agent-bus:11165",
    )
    assert "fired by cdp generate on agent-bus:11165" in text
    assert "scheduled task liaison-wake-10479" not in text


_SEATING_KW = {
    "ring": "10532",
    "extra_addresses": (
        "cortex://notes/system/threads/10479-charter-scoreboard.md#Loop",
    ),
    "fired_by": "cdp generate on agent-bus:11165, not a scheduled task",
}


@pytest.mark.offline
def test_commission_shed_is_atomic_under_cap() -> None:
    """Cap pressure must drop the whole commission line, not its guard alone."""
    text = render_doorbell(
        "10479",
        "claude-ai-navigator-seat",
        seat="cursor-sdk",
        **_SEATING_KW,
        cap=954,
    )
    assert "commission:" not in text
    assert "if attention mint" not in text
    assert "parent_thread=10479" not in text


@pytest.mark.offline
def test_live_10479_web_anthropic_render_byte_length() -> None:
    """AC4 — 11655 regression pin corrected: life render is not the old 1019 B IDE paste."""
    text = _live_10479_render()
    encoded_len = len(text.encode("utf-8"))
    assert encoded_len == 1085
    assert encoded_len != 1019
    assert not re.search(r"^Use the liaison skill\.$", text, re.MULTILINE)


@pytest.mark.offline
def test_basis_floor_guard_under_cap() -> None:
    floor = basis_floor_bytes(
        "10479",
        "claude-ai-navigator-seat",
        attention_row_ids=("10586",),
        as_of="2026-09-14T07:00:00Z",
        digest_source="a" * 16,
        scope_lanes=("10532",),
        fingerprint="b" * 16,
    )
    assert floor <= DOORBELL_CAP - 128


@pytest.mark.offline
def test_quintuple_render_fits_cap_without_shedding() -> None:
    text = render_doorbell(
        "10479",
        "claude-ai-navigator-seat",
        ring="10532",
        fired_by="cdp generate via liaison-ticker",
        include_commission=True,
        attention_row_ids=("10586",),
        as_of="2026-09-14T07:00:00Z",
        digest_source="abc123def4567890",
        scope_lanes=("10532",),
        fingerprint="abc123def4567890",
    )
    encoded = len(text.encode("utf-8"))
    assert encoded <= DOORBELL_CAP
    assert "value=10586" in text
    assert "epoch=abc123def4567890" in text
    assert "STALE" in text
    assert "tools: <count>" not in text


@pytest.mark.offline
def test_include_commission_keyword_omits_line() -> None:
    text = render_doorbell(*_DEFAULT_ARGS, **_DEFAULT_KW, include_commission=False)
    assert "commission:" not in text
    assert "if attention mint" not in text


@pytest.mark.offline
def test_commission_guard_present_whenever_commission_line_is() -> None:
    """The conditional guard and commission line are inseparable."""
    caps = (8192, DOORBELL_CAP, 954, 955, 1000)
    for cap in caps:
        text = render_doorbell(
            "10479",
            "claude-ai-navigator-seat",
            seat="cursor-sdk",
            **_SEATING_KW,
            cap=cap,
        )
        if "commission:" in text:
            assert "if attention mint" in text
        else:
            assert "if attention mint" not in text


_SUCCESSOR_KW = {
    "gear": "3-wake-on-attention",
    "row": "Settled · Live · Next",
    "tip_turn": 42,
    "tip_checkpoint_turn": 40,
    "spawn_signal_sources": ["checkpoint_due"],
}

_SUCCESSOR_WAKE_GOLDEN = (
    "resume 10479\n\n"
    "WAKE — liaison headless successor, house agent-bus:10479 — contract: none.\n"
    "duty: run the tick; checkpoint; hop only if hop_qualifies. "
    "Hop only when autonomous follow-up remains; HOLD_MERGE / empty NOW / quiet tick → STAY.\n"
    "disclosure: orientation ritual; one echo before the first move.\n"
    "objective: tip turn #42 on agent-bus:10479; tip CHECKPOINT #40; "
    "row=Settled · Live · Next; gear: 3-wake-on-attention; spawn_signal=checkpoint_due.\n"
    'addresses: dispatch(tool="continuity", arguments=\'{"op":"resume","thread":"10479"}\'); '
    "agent_bus_read(thread_get, thread=10479); agent-bus:10479 (echo)\n"
    "Use the liaison skill. LOAD the liaison skill body; do not skim.\n"
    "LOAD AND EXECUTE runbook:bus-consult-watcher (legs 1-3).\n"
    "frame: spawned by liaison-ticker gear 3-wake-on-attention; seat cursor-sdk; "
    "predecessor = prior lease holder on agent-bus:10479. "
    "§ Peer-house: keep both; cdp/opus-5 → 2nd pool → cursor/claude-opus-5; "
    "¬ cursor/claude-fable-5-1; ¬ hop away unreconciled.\n"
    'echo: agent_bus(send, thread=10479, subject="ORIENTED 10479", '
    'body="ORIENTED / tip: <CHECKPOINT subject> cp_ordinal=<n> / row: <row> / seat: cursor-sdk") '
    "before the first mutating move.\n"
)


def _default_successor_render() -> str:
    return render_successor_wake("10479", **_SUCCESSOR_KW)


@pytest.mark.offline
def test_successor_wake_sheds_overlong_row_without_raising() -> None:
    """Regression for a:33659 — verbose now_row must not crash-loop the ticker."""
    long_row = "R" + (" verbose policy bind " * 80)
    unshed = successor_wake_unshed_byte_length(
        "10479", gear="3-wake-on-attention", row=long_row
    )
    assert unshed > SUCCESSOR_WAKE_CAP
    text = render_successor_wake(
        "10479",
        gear="3-wake-on-attention",
        row=long_row,
        tip_turn=1,
        tip_checkpoint_turn=1,
    )
    assert len(text.encode("utf-8")) <= SUCCESSOR_WAKE_CAP


@pytest.mark.offline
def test_successor_wake_fitting_input_byte_identical() -> None:
    first = _default_successor_render()
    second = _default_successor_render()
    assert first == second
    assert len(first.encode("utf-8")) == 1173


@pytest.mark.offline
def test_successor_wake_default_contract_byte_identical_to_golden() -> None:
    """AC1.4 — no contract arg must match pre-change golden paste."""
    assert render_successor_wake("10479", **_SUCCESSOR_KW) == _SUCCESSOR_WAKE_GOLDEN


@pytest.mark.offline
def test_successor_wake_conductor_contract_duty_line() -> None:
    """AC1.3 — conductor contract swaps duty line; no hop_qualifies."""
    text = render_successor_wake("10479", **_SUCCESSOR_KW, contract="conductor")
    assert "— contract: conductor." in text
    assert "dispatch -> read back -> verify -> CP" in text
    assert "hop only if hop_qualifies" not in text


@pytest.mark.offline
def test_successor_wake_conductor_contract_survives_row_shedding() -> None:
    """AC1.5 — contract line is never shed."""
    long_row = "z" * 5000
    text = render_successor_wake(
        "10479",
        gear="3-wake-on-attention",
        row=long_row,
        tip_turn=1,
        tip_checkpoint_turn=1,
        contract="conductor",
    )
    assert "— contract: conductor." in text
    assert len(text.encode("utf-8")) <= SUCCESSOR_WAKE_CAP


@pytest.mark.offline
def test_successor_wake_truncation_marker_only_when_shed() -> None:
    fitting = _default_successor_render()
    assert "..." not in fitting
    long_row = "x" * 3000
    shed = render_successor_wake(
        "10479",
        gear="3-wake-on-attention",
        row=long_row,
        tip_turn=1,
        tip_checkpoint_turn=1,
    )
    assert "..." in shed
    assert f"row={long_row}" not in shed


_10479_CDP_SUCCESSOR_KW = {
    "gear": "4-cdp-liaison",
    "row": "Settled · Live · Next",
    "seat": "cdp",
    "tip_turn": 42,
    "tip_checkpoint_turn": 40,
    "spawn_signal_sources": ["checkpoint_due"],
}


@pytest.mark.offline
def test_successor_wake_cdp_10479_inlines_liaison_body_without_use_line() -> None:
    """AC4 — rendered CDP successor wake seals liaison SOT; no Customize Use-line."""
    rendered = render_successor_wake("10479", **_10479_CDP_SUCCESSOR_KW)
    assert "harvests → folds → decides → dispatches → checkpoints → hops" in rendered
    assert "Use the liaison skill" not in rendered
    assert '<skill slug="liaison"' in rendered
    assert "<skills_inline>" in rendered


@pytest.mark.offline
def test_successor_wake_cdp_omits_use_the_liaison_line() -> None:
    text = render_successor_wake("10479", **_10479_CDP_SUCCESSOR_KW)
    assert "Use the liaison skill" not in text
    assert "LOAD the liaison skill body" not in text


@pytest.mark.offline
def test_successor_wake_load_bearing_fields_survive_maximal_shedding() -> None:
    long_row = "y" * 5000
    text = render_successor_wake(
        "10479",
        gear="3-wake-on-attention",
        row=long_row,
        tip_turn=99,
        tip_checkpoint_turn=88,
        ring="10532",
    )
    assert "resume 10479" in text
    assert (
        'dispatch(tool="continuity", arguments=\'{"op":"resume","thread":"10479"}\')'
        in text
    )
    assert "agent_bus_read(thread_get, thread=10479)" in text
    assert "agent-bus:10532 (echo)" in text
    assert "tip turn #99" in text
    assert "tip CHECKPOINT #88" in text
    assert "gear: 3-wake-on-attention" in text
    assert "Use the liaison skill." in text
    assert "runbook:bus-consult-watcher" in text
    assert "seat cursor-sdk" in text
