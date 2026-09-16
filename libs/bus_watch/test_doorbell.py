"""Tests for the WAKE doorbell renderer."""

from __future__ import annotations

import pytest

from bus_watch.doorbell import (
    DOORBELL_CAP,
    SUCCESSOR_WAKE_CAP,
    basis_floor_bytes,
    render_address,
    render_doorbell,
    render_successor_wake,
    successor_wake_unshed_byte_length,
)

_DEFAULT_ARGS = ("10479", "liaison-autonomous-night")
_DEFAULT_KW = {"ring": "10532"}


def _default_render() -> str:
    return render_doorbell(*_DEFAULT_ARGS, **_DEFAULT_KW)


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
        **_SEATING_KW,
        cap=954,
    )
    assert "commission:" not in text
    assert "if attention mint" not in text
    assert "parent_thread=10479" not in text


@pytest.mark.offline
def test_default_render_byte_identical_1019() -> None:
    text = _default_render()
    assert len(text.encode("utf-8")) == 1019


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


def _default_successor_render() -> str:
    return render_successor_wake("10479", **_SUCCESSOR_KW)


@pytest.mark.offline
def test_successor_wake_sheds_overlong_row_without_raising() -> None:
    """Regression for a:33659 — verbose now_row must not crash-loop the ticker."""
    long_row = "R" + (" verbose policy bind " * 80)
    unshed = successor_wake_unshed_byte_length("10479", gear="3-wake-on-attention", row=long_row)
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
    assert 'dispatch(tool="continuity", arguments=\'{"op":"resume","thread":"10479"}\')' in text
    assert "agent_bus_read(thread_get, thread=10479)" in text
    assert "agent-bus:10532 (echo)" in text
    assert "tip turn #99" in text
    assert "tip CHECKPOINT #88" in text
    assert "gear: 3-wake-on-attention" in text
    assert "Use the liaison skill." in text
    assert "runbook:bus-consult-watcher" in text
    assert "seat cursor-sdk" in text
