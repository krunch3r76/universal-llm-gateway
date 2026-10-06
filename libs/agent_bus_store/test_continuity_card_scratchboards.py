"""Tests for continuity-card scratchboard and Skills section parsing."""

from __future__ import annotations

from agent_bus_store.continuity_card_scratchboards import (
    extract_card_skills,
    extract_scratchboard_uris,
    missing_required_headings,
    scratchboard_uri,
    validate_continuity_card,
)

pytestmark = __import__("pytest").mark.offline

_CARD = """
## Skills
- `outbound-voice-spec`
- `prose-discipline`

## Stance
Use the `ulg-for-llms` skill.

## Why this house
test

## Objective
Mission

## Runbooks
- runbook:test

## Rules
- _None yet._

## Sidecars
- cortex://notes/system/threads/9732-opportunities.md

## Scratchboards
- cortex://notes/system/scratchboards/9732-calendar-xvfb-scratchboard.md — Leg B

## House
document:9732-continuity
"""


def test_extract_scratchboard_uris() -> None:
    uris = extract_scratchboard_uris(_CARD)
    assert uris == [
        "cortex://notes/system/scratchboards/9732-calendar-xvfb-scratchboard.md"
    ]


def test_scratchboard_uri_helper() -> None:
    assert scratchboard_uri("9732", "calendar-xvfb") == (
        "cortex://notes/system/scratchboards/9732-calendar-xvfb-scratchboard.md"
    )


def test_validate_continuity_card_ok() -> None:
    assert validate_continuity_card(_CARD) == []


def test_missing_required_headings_flags_scratchboards() -> None:
    missing = missing_required_headings(_CARD.replace("## Scratchboards", ""))
    assert "## Scratchboards" in missing


def test_missing_required_headings_flags_skills() -> None:
    missing = missing_required_headings(_CARD.replace("## Skills", ""))
    assert "## Skills" in missing
    assert "missing_heading:skills" in validate_continuity_card(
        _CARD.replace("## Skills\n- `outbound-voice-spec`\n- `prose-discipline`\n", "")
    )


def test_extract_card_skills_bullets_and_order() -> None:
    assert extract_card_skills(_CARD) == [
        "outbound-voice-spec",
        "prose-discipline",
    ]


def test_extract_card_skills_table_slash_and_paren() -> None:
    card = """
## Skills
| slug | note |
| --- | --- |
| /outbound-voice-spec (fallback .claude/skills/…) | Fonzi |
| prose-discipline (local) | SMS |
| not a slug row because spaces everywhere | skip |

## Stance
x
"""
    assert extract_card_skills(card) == [
        "outbound-voice-spec",
        "prose-discipline",
    ]


def test_extract_card_skills_malformed_rows_skipped() -> None:
    card = """
## Skills
-
| |
| --- |
| !!! |
- `good-skill`

## House
x
"""
    assert extract_card_skills(card) == ["good-skill"]


def test_extract_card_skills_missing_section() -> None:
    assert extract_card_skills("## Stance\nUse ulg-for-llms\n") == []


def test_extract_card_skills_empty_section() -> None:
    card = "## Skills\n\n## Stance\nx\n"
    assert extract_card_skills(card) == []
    assert "empty_skills_section" in validate_continuity_card(
        _CARD.replace(
            "## Skills\n- `outbound-voice-spec`\n- `prose-discipline`\n",
            "## Skills\n",
        )
    )
