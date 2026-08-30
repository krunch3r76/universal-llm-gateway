"""Hermetic tests for compose chip gate-reject capture + dual-id emit (arc 6928)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from claude_bundles.compose_chip_probe import (
    _size_reject,
    collect_effort_candidates,
    try_click_compose_chip,
)
from claude_bundles.events_compose_attest import (
    emit_compose_attested,
    emit_compose_attested_from_result,
)


@pytest.fixture(autouse=True)
def _silence_compose_attest_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    from claude_bundles import events_compose_attest as eca

    monkeypatch.setattr(eca, "_mirror_to_event_service", lambda _event: None)


def test_size_reject_bounds() -> None:
    assert _size_reject(None) == {"reason": "no_box"}
    assert _size_reject({"width": 10, "height": 20})["reason"] == "size"
    assert _size_reject({"width": 40, "height": 20}) is None


@pytest.mark.asyncio
async def test_collect_effort_candidates_returns_dom_rows() -> None:
    """a:31333 — census matches Effort vocabulary; tolerates a PUA-suffixed row.

    Fixture mirrors the exact live-observed row from production (a real
    trailing ``U+E02A`` chevron glyph) — the row must still come through the
    census as a normal candidate, codepoints included, not get filtered out.
    """
    page = AsyncMock()
    page.evaluate = AsyncMock(
        return_value=[
            {
                "tag": "DIV",
                "role": "menuitem",
                "aria": "",
                "text": "Effort\nExtra\n\ue02a",
                "codepoints": ["U+0045", "U+0066", "U+0066", "U+E02A"],
                "offsetParent": True,
                "w": 200.0,
                "h": 32.0,
            }
        ]
    )
    rows = await collect_effort_candidates(page)
    assert len(rows) == 1
    assert rows[0]["role"] == "menuitem"
    assert rows[0]["text"] == "Effort\nExtra\n\ue02a"
    assert rows[0]["codepoints"][-1] == "U+E02A"
    page.evaluate.assert_awaited_once()


def test_emit_compose_attested_dual_ids_every_row() -> None:
    event = emit_compose_attested(
        ok=True,
        surface="bare_new",
        step="cowork_auto",
        via="playwright_surface",
        wanted="cowork",
        chip_candidate_count=1,
        surface_radiogroup_count=1,
        radiogroup_names=["Surface"],
        gate_rejects=[],
        polled_ms=800,
        fingerprint={"title": "New task - Claude", "mode": "cowork"},
        execution_id="0b692df9-bb65-419f-8d08-7c5887eb0837",
        satellite_execution_id="8e248e70846b4765af68d57b270b4825",
    )
    assert event is not None
    assert event.signal == "cdp.generate.compose_attested"
    payload = event.payload
    assert payload["ok"] is True
    assert payload["execution_id"] == "0b692df9-bb65-419f-8d08-7c5887eb0837"
    assert payload["satellite_execution_id"] == "8e248e70846b4765af68d57b270b4825"
    assert payload["execution_id"] != payload["satellite_execution_id"]
    assert payload["surface_radiogroup_count"] == 1
    assert payload["radiogroup_names"] == ["Surface"]


def test_emit_failure_arm_keeps_both_id_keys() -> None:
    event = emit_compose_attested(
        ok=False,
        step="chip_missing",
        gate_rejects=[{"arm": "surface", "reason": "size", "w": 12, "h": 10}],
        surface_radiogroup_count=1,
        radiogroup_names=["Surface"],
        chip_candidate_count=0,
        execution_id="sg-uuid",
        satellite_execution_id="sathex0123456789abcdef0123456789",
    )
    assert event is not None
    p = event.payload
    assert p["ok"] is False
    assert "execution_id" in p and "satellite_execution_id" in p
    assert p["gate_rejects"][0]["reason"] == "size"


def test_emit_from_result_projects_mode_block() -> None:
    result = {
        "ok": False,
        "step": "cowork",
        "mode": {
            "ok": False,
            "step": "chip_missing",
            "wanted": "cowork",
            "candidates": [],
            "surface_radiogroup_count": 1,
            "radiogroup_names": ["Surface"],
            "gate_rejects": [{"arm": "surface", "reason": "not_visible"}],
            "compose_mode_fingerprint": {"mode": "chat", "title": "New chat - Claude"},
            "polled_ms": 8000,
        },
    }
    event = emit_compose_attested_from_result(
        result,
        execution_id="0b692df9-bb65-419f-8d08-7c5887eb0837",
        satellite_execution_id="8e248e70846b4765af68d57b270b4825",
    )
    assert event is not None
    assert event.payload["ok"] is False
    assert event.payload["step"] == "cowork"
    assert event.payload["gate_rejects"][0]["reason"] == "not_visible"
    assert event.payload["execution_id"].startswith("0b692df9")


@pytest.mark.offline
def test_emit_from_result_prefers_approval_after_not_mode_manual() -> None:
    """Cowork+Auto success must not emit the pre-flip Manual mode fingerprint."""
    result = {
        "ok": True,
        "step": "cowork_auto",
        "mode": {
            "ok": True,
            "step": "selected_cowork",
            "after": {
                "title": "New task - Claude",
                "mode": "cowork",
                "approval": {"aria": "Manually approve", "text": "Manual"},
                "url": "https://claude.ai/new",
            },
        },
        "approval": {
            "ok": True,
            "step": "selected_auto",
            "after": {
                "title": "New task - Claude",
                "mode": "cowork",
                "approval": {"aria": "Automatically approve", "text": "Auto"},
                "url": "https://claude.ai/new",
            },
        },
    }
    event = emit_compose_attested_from_result(
        result,
        execution_id="sg-uuid",
        satellite_execution_id="sathex0123456789abcdef0123456789",
    )
    assert event is not None
    fp = event.payload["fingerprint"]
    assert fp["approval"]["aria"] == "Automatically approve"
    assert event.payload["ok"] is True


@pytest.mark.asyncio
async def test_try_click_records_size_gate_reject() -> None:
    """Click path size reject is captured — not silent continue."""
    radio = AsyncMock()
    radio.is_visible = AsyncMock(return_value=True)
    radio.bounding_box = AsyncMock(
        return_value={"width": 12, "height": 10, "x": 0, "y": 0}
    )

    scoped = AsyncMock()
    scoped.count = AsyncMock(return_value=1)
    scoped.first = radio

    surface_rg = AsyncMock()
    surface_rg.get_by_role = lambda *a, **k: scoped

    empty = AsyncMock()
    empty.count = AsyncMock(return_value=0)

    def _get_by_role(role, name=None):
        if role == "radiogroup":
            return surface_rg
        return empty

    page = AsyncMock()
    page.get_by_role = _get_by_role
    page.get_by_text = lambda *a, **k: empty
    page.evaluate = AsyncMock(
        side_effect=[
            {
                "radiogroup_names": ["Surface"],
                "surface_radiogroup_count": 1,
                "radiogroup_count": 1,
            },
            None,  # chip_center
        ]
    )

    via, probe = await try_click_compose_chip(page, "Cowork")
    assert via is None
    assert any(r.get("reason") == "size" for r in probe["gate_rejects"])
    assert probe["surface_radiogroup_count"] == 1
    assert "Surface" in probe["radiogroup_names"]
