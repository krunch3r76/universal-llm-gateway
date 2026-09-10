"""Tests for continuity-card scratchboard section parsing."""

from __future__ import annotations

from agent_bus_store.continuity_card_scratchboards import (
    extract_scratchboard_uris,
    missing_required_headings,
    scratchboard_uri,
    validate_continuity_card,
)

pytestmark = __import__("pytest").mark.offline

_CARD = """
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
