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


def test_non_code_not_on_rail_observes_nothing_when_attached_empty() -> None:
    """Absent rail is not an observation: expected slugs must not come back (R2 F2)."""
    exc = ChatContextSkillsError(_NOT_ON_RAIL)
    assert (
        recover_induction_slugs_on_rail_error(
            exc,
            claude_surface=None,
            attached=[],
            induction_slugs=_INDUCTION,
        )
        == []
    )


def test_non_code_not_on_rail_observes_nothing_when_attached_non_empty() -> None:
    """Attached chips already reach attest via ``attached=``; induction stays empty."""
    exc = ChatContextSkillsError(_NOT_ON_RAIL)
    assert (
        recover_induction_slugs_on_rail_error(
            exc,
            claude_surface="cowork",
            attached=_ATTACHED,
            induction_slugs=_INDUCTION,
        )
        == []
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


def test_code_surface_not_on_rail_observes_nothing() -> None:
    exc = ChatContextSkillsError(_NOT_ON_RAIL)
    assert (
        recover_induction_slugs_on_rail_error(
            exc,
            claude_surface="code",
            attached=[],
            induction_slugs=_INDUCTION,
        )
        == []
    )


def test_code_surface_still_collapsed_observes_nothing() -> None:
    exc = ChatContextSkillsError(_STILL_COLLAPSED)
    assert (
        recover_induction_slugs_on_rail_error(
            exc,
            claude_surface="code",
            attached=_ATTACHED,
            induction_slugs=_INDUCTION,
        )
        == []
    )


def test_code_surface_no_toggle_observes_nothing() -> None:
    exc = ChatContextSkillsError(_NO_TOGGLE)
    assert (
        recover_induction_slugs_on_rail_error(
            exc,
            claude_surface="code",
            attached=[],
            induction_slugs=_INDUCTION,
        )
        == []
    )


def _stub_shared_sync_catalog(
    monkeypatch: pytest.MonkeyPatch, slugs: list[str]
) -> list[dict]:
    """Hermetic catalog (all ``shared_sync``) + captured attest events."""
    from claude_bundles import cowork_skill_delivery
    from claude_bundles.catalog import SkillCatalog, SkillCatalogEntry

    catalog = SkillCatalog(
        entries={
            s: SkillCatalogEntry(
                slug=s, surface_class="shared_sync", mcp_surface_required="none"
            )
            for s in slugs
        },
        alias_to_canonical={},
    )
    events: list[dict] = []
    monkeypatch.setattr(cowork_skill_delivery, "get_skill_catalog", lambda: catalog)
    monkeypatch.setattr(
        cowork_skill_delivery,
        "emit_skill_delivery_attested",
        lambda **kw: events.append(kw),
    )
    return events


@pytest.mark.parametrize("surface", [None, "cowork", "code"])
def test_missing_rail_cannot_yield_passing_attest(
    monkeypatch: pytest.MonkeyPatch, surface: str | None
) -> None:
    """Recovered list fed to attest as ``induction=`` must fail closed (R2 F2)."""
    from claude_bundles.cowork_skill_delivery import (
        SkillDeliveryError,
        attest_delivery_channels,
    )

    events = _stub_shared_sync_catalog(monkeypatch, _INDUCTION)
    recovered = recover_induction_slugs_on_rail_error(
        ChatContextSkillsError(_NOT_ON_RAIL),
        claude_surface=surface,
        attached=[],
        induction_slugs=_INDUCTION,
    )
    assert recovered is not None
    with pytest.raises(SkillDeliveryError, match="undelivered="):
        attest_delivery_channels(
            _INDUCTION, attached=[], inlined=[], induction=recovered
        )
    assert [e["ok"] for e in events] == [False]


def test_missing_rail_with_real_attach_still_attests_via_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slugs proven by composer chips pass on ``attached=``, not on the rail."""
    from claude_bundles.cowork_skill_delivery import attest_delivery_channels

    events = _stub_shared_sync_catalog(monkeypatch, _INDUCTION)
    recovered = recover_induction_slugs_on_rail_error(
        ChatContextSkillsError(_NOT_ON_RAIL),
        claude_surface=None,
        attached=_INDUCTION,
        induction_slugs=_INDUCTION,
    )
    attest_delivery_channels(
        _INDUCTION, attached=_INDUCTION, inlined=[], induction=recovered
    )
    assert events[-1]["ok"] is True
    assert events[-1]["induction"] == []
