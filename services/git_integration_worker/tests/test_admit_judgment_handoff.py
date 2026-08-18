"""Admit classifies judgment-bearing implement off pure-mechanical.

Detector is opt-in: unmarked implement and density:mechanical stay
pure-mechanical. Line-start RULING ACs raise to light-bounded.
"""

from __future__ import annotations

import pytest
from reasoning_posture_contracts import REASONING_POSTURE_SKIP_CONTRACTS

from services.git_integration_worker.cursor_auto.directive import body_declares_judgment
from services.git_integration_worker.cursor_auto.wire_map import (
    resolve_handoff_contract,
)

_MECHANICAL = (
    "TYPE: DIRECTIVE\ndensity: mechanical\nscope: libs/foo\nImplement the thing."
)
_DENSE_UNMARKED = (
    "TYPE: DIRECTIVE\ndensity: dense\ncontract: implement\n"
    "scope: libs/foo\nImplement the bound one-liner."
)
_TURN_302 = (
    "TYPE: DIRECTIVE\ncontract: implement\ndensity: dense\nscope: libs/foo\n"
    "RULING AC1 — bind the detector; do not flip unmarked implement.\n"
    "RULING AC2 — do not touch REASONING_POSTURE_SKIP_CONTRACTS.\n"
)
_PROSE_MENTION = (
    "TYPE: DIRECTIVE\ndensity: dense\nscope: libs/foo\n"
    "The seed named three candidate detectors — frontmatter, RULING ACs, "
    "density tokens — and deliberately did not pick.\n"
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
        ("", False),
        (None, False),
        (_TURN_302, True),
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


@pytest.mark.offline
def test_ruling_acs_raise_to_light_bounded() -> None:
    assert resolve_handoff_contract("implement", body=_TURN_302) == "light-bounded"


@pytest.mark.offline
def test_non_implement_contracts_unchanged() -> None:
    assert resolve_handoff_contract("investigate", body=_TURN_302) == "light-bounded"
    assert resolve_handoff_contract("seed") == "light-bounded"
    assert resolve_handoff_contract("confer") == "light-bounded"
