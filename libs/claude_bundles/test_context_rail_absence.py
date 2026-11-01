"""Hermetic tests for post-submit Context rail absence recovery."""

from __future__ import annotations

import pytest

from claude_bundles.chat_context_skills import ChatContextSkillsError
from claude_bundles.context_rail_absence import recover_induction_slugs_on_rail_error

pytestmark = pytest.mark.offline

_NOT_ON_RAIL = (
    "Context list is not on the rail \u2014 scrape refused until it is expanded"
)
_STILL_COLLAPSED = (
    "Context list is still collapsed after open "
    "(aria-expanded='false', list_visible=False) — scrape refused"
)
_NO_TOGGLE = "Context list is collapsed and has no toggle — scrape refused"
_INDUCTION = ["reasoning-posture", "ulg-architecture"]
_ATTACHED = ["architecture-invariants"]


def test_non_code_not_on_rail_uses_induction_slugs_when_attached_empty() -> None:
    exc = ChatContextSkillsError(_NOT_ON_RAIL)
    assert (
        recover_induction_slugs_on_rail_error(
            exc,
            claude_surface=None,
            attached=[],
            induction_slugs=_INDUCTION,
        )
        == _INDUCTION
    )


def test_non_code_not_on_rail_prefers_attached_when_non_empty() -> None:
    exc = ChatContextSkillsError(_NOT_ON_RAIL)
    assert (
        recover_induction_slugs_on_rail_error(
            exc,
            claude_surface="cowork",
            attached=_ATTACHED,
            induction_slugs=_INDUCTION,
        )
        == _ATTACHED
    )


def test_none_surface_still_collapsed_returns_none() -> None:
    exc = ChatContextSkillsError(_STILL_COLLAPSED)
    assert (
        recover_induction_slugs_on_rail_error(
            exc,
            claude_surface=None,
            attached=[],
            induction_slugs=_INDUCTION,
        )
        is None
    )


def test_none_surface_no_toggle_returns_none() -> None:
    exc = ChatContextSkillsError(_NO_TOGGLE)
    assert (
        recover_induction_slugs_on_rail_error(
            exc,
            claude_surface=None,
            attached=[],
            induction_slugs=_INDUCTION,
        )
        is None
    )


def test_non_chat_context_skills_error_returns_none() -> None:
    assert (
        recover_induction_slugs_on_rail_error(
            RuntimeError(_NOT_ON_RAIL),
            claude_surface=None,
            attached=[],
            induction_slugs=_INDUCTION,
        )
        is None
    )


def test_code_surface_not_on_rail_returns_slug_list() -> None:
    exc = ChatContextSkillsError(_NOT_ON_RAIL)
    assert (
        recover_induction_slugs_on_rail_error(
            exc,
            claude_surface="code",
            attached=[],
            induction_slugs=_INDUCTION,
        )
        == _INDUCTION
    )


def test_code_surface_still_collapsed_returns_slug_list() -> None:
    exc = ChatContextSkillsError(_STILL_COLLAPSED)
    assert (
        recover_induction_slugs_on_rail_error(
            exc,
            claude_surface="code",
            attached=_ATTACHED,
            induction_slugs=_INDUCTION,
        )
        == _ATTACHED
    )
