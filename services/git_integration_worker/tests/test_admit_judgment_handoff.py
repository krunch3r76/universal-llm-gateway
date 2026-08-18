"""Admit classifies judgment-bearing implement off pure-mechanical.

Detector is opt-in: unmarked implement and density:mechanical stay
pure-mechanical. Line-start or AC-label ``RULING`` raises to light-bounded.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from reasoning_posture_contracts import REASONING_POSTURE_SKIP_CONTRACTS

from services.git_integration_worker.cursor_auto.directive import body_declares_judgment
from services.git_integration_worker.cursor_auto.wire_map import (
    resolve_handoff_contract,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_TURN_302 = (_FIXTURES / "agent_bus_9470_turn_302.txt").read_text(encoding="utf-8")
_TURN_343 = (_FIXTURES / "agent_bus_9470_turn_343.txt").read_text(encoding="utf-8")
# Bus GET /turns/by-number thread=9470 turn 302/343 (UDS, 2026-08-18).
_TURN_302_SHA256 = "95ae627e1a714f0d800a96c3cb8010ba652e903a01bd55b7e8af6ecaa095162b"
_TURN_343_SHA256 = "03eec2976967ed811ab9f6a12474d5cbc8e86f5b346df7c63d6010c3080ab28a"

_MECHANICAL = (
    "TYPE: DIRECTIVE\ndensity: mechanical\nscope: libs/foo\nImplement the thing."
)
_DENSE_UNMARKED = (
    "TYPE: DIRECTIVE\ndensity: dense\ncontract: implement\n"
    "scope: libs/foo\nImplement the bound one-liner."
)
_PROSE_MENTION = (
    "TYPE: DIRECTIVE\ndensity: dense\nscope: libs/foo\n"
    "The seed named three candidate detectors — frontmatter, RULING ACs, "
    "density tokens — and deliberately did not pick.\n"
)
_PROSE_RULING_WORD = (
    "TYPE: DIRECTIVE\ndensity: dense\nscope: libs/foo\n"
    "Do not treat a mid-sentence mention of the word RULING or ruling as a marker.\n"
)


@pytest.mark.offline
def test_skip_set_unchanged() -> None:
    assert REASONING_POSTURE_SKIP_CONTRACTS == frozenset(
        {"implement", "pure-mechanical", "propagate", "execute", "answer"}
    )


@pytest.mark.offline
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (_MECHANICAL, False),
        (_DENSE_UNMARKED, False),
        (_PROSE_MENTION, False),
        (_PROSE_RULING_WORD, False),
        (_TURN_343, False),
        ("", False),
        (None, False),
        (_TURN_302, True),
        (
            "TYPE: DIRECTIVE\ndensity: dense\nscope: libs/foo\n"
            "RULING AC1 — bind the detector; do not flip unmarked implement.\n",
            True,
        ),
        (
            "TYPE: DIRECTIVE\ndensity: judgment_required\nscope: libs/foo\nFix it.",
            True,
        ),
        (
            "TYPE: DIRECTIVE\ndensity_triage: judgment_required\nscope: libs/foo\n",
            True,
        ),
        (
            "TYPE: DIRECTIVE\ndensity: investigate\nscope: libs/foo\nLook.",
            True,
        ),
        (
            "TYPE: DIRECTIVE\ndensity: dense\nscope: libs/foo\n"
            "## Open fork\nPick the detector.\n",
            True,
        ),
        (
            "TYPE: DIRECTIVE\ndensity: dense\nscope: libs/foo\n"
            "handoff: light-bounded\n",
            True,
        ),
        (
            "TYPE: DIRECTIVE\ndensity: dense\nscope: libs/foo\n"
            "handoff: pure-mechanical\n",
            False,
        ),
    ],
)
def test_body_declares_judgment(body: str | None, expected: bool) -> None:
    assert body_declares_judgment(body) is expected


@pytest.mark.offline
def test_unmarked_implement_stays_pure_mechanical() -> None:
    assert resolve_handoff_contract("implement") == "pure-mechanical"
    assert resolve_handoff_contract("implement", body=_MECHANICAL) == (
        "pure-mechanical"
    )
    assert resolve_handoff_contract("implement", body=_DENSE_UNMARKED) == (
        "pure-mechanical"
    )
    assert resolve_handoff_contract("implement", body=_PROSE_MENTION) == (
        "pure-mechanical"
    )
    assert resolve_handoff_contract("implement", body=_PROSE_RULING_WORD) == (
        "pure-mechanical"
    )
    assert resolve_handoff_contract("implement", body=_TURN_343) == (
        "pure-mechanical"
    )


@pytest.mark.offline
def test_turn_302_specimen_is_bus_verbatim() -> None:
    assert hashlib.sha256(_TURN_302.encode("utf-8")).hexdigest() == _TURN_302_SHA256
    assert "AC5 — RULING," in _TURN_302
    assert not _TURN_302.lstrip().startswith("RULING")


@pytest.mark.offline
def test_turn_343_specimen_is_bus_verbatim() -> None:
    assert hashlib.sha256(_TURN_343.encode("utf-8")).hexdigest() == _TURN_343_SHA256
    assert "**Before you pick," in _TURN_343


@pytest.mark.offline
def test_ruling_acs_raise_to_light_bounded() -> None:
    assert resolve_handoff_contract("implement", body=_TURN_302) == "light-bounded"


@pytest.mark.offline
def test_non_implement_contracts_unchanged() -> None:
    assert resolve_handoff_contract("investigate", body=_TURN_302) == "light-bounded"
    assert resolve_handoff_contract("seed") == "light-bounded"
    assert resolve_handoff_contract("confer") == "light-bounded"
