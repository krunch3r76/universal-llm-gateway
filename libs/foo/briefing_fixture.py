"""Shared fixtures for cursor-auto first-episode admit BRIEFING tests.

Extracted from ``test_cursor_auto_episode_briefing`` so nested-dispatch
dogfood scopes can target ``libs/foo`` without duplicating turn shapes.
"""

from __future__ import annotations

from typing import Any

BRIEFING_HEADER = "TYPE: BRIEFING"
MAX_BRIEFING_LINES = 26

FROM_AUTO = "cursor-auto"
ADMIT_SUBJECT_PREFIX = "status:admitted"
ADMIT_SUBJECT_PRIOR = f"{ADMIT_SUBJECT_PREFIX} — prior"
BASE_ADMIT_BODY = "Auto admitted lane:cursor-auto request."
MINI_BRIEFING_SAMPLE = f"{BRIEFING_HEADER}\nops offer"

CODE_WORK_CONTRACTS = frozenset({"implement", "investigate", "verify"})

BRIEFING_REQUIRED_SUBSTRINGS = (
    "cursor-auto lane",
    "front-door bind",
    "needs-attended",
    "live_deltas",
    "NEW CDP WINDOW",
    "handoff_prompt",
    "SAME private lane",
)

BRIEFING_FORBIDDEN_SUBSTRINGS = (
    "manage / charter_reload",
    "contract=confer",
    "Codebase work",
    "you lack",
    "don't have",
)

TURNS_NO_PRIOR_ADMITS: list[dict[str, Any]] = [
    {"from": "web-anthropic", "subject": "TYPE: DIRECTIVE"},
    {"from": "cursor", "subject": "status:done — foo"},
]

TURNS_PRIOR_ADMIT: list[dict[str, Any]] = [
    {"from": FROM_AUTO, "subject": f"{ADMIT_SUBJECT_PREFIX} — earlier"},
    {"from": "web-anthropic", "subject": "TYPE: DIRECTIVE"},
]

TURNS_FIRST_EPISODE: list[dict[str, Any]] = [
    {"from": "web-anthropic", "subject": "TYPE: DIRECTIVE"},
]

TURNS_FOLLOW_ON: list[dict[str, Any]] = [
    {"from": FROM_AUTO, "subject": ADMIT_SUBJECT_PRIOR},
]
