"""Tests for the WAKE doorbell renderer."""

from __future__ import annotations

import pytest

from bus_watch.doorbell import DOORBELL_CAP, render_doorbell

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
def test_cap_enforced_and_overridable() -> None:
    long_extras = tuple(
        f"cortex://notes/system/threads/file-{idx}.md" for idx in range(40)
    )
    with pytest.raises(ValueError, match="1024"):
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
