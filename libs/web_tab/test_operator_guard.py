"""Operator-active probe — focus/visible refusal for non-agent tabs."""

from unittest.mock import AsyncMock, patch

import pytest

from web_tab.operator_guard import operator_active_probe


@pytest.mark.asyncio
async def test_focus_refused_when_tab_not_created_by_agent() -> None:
    with (
        patch(
            "web_tab.operator_guard.install_operator_activity_probe",
            AsyncMock(),
        ),
        patch(
            "web_tab.operator_guard.wait_for_operator_idle",
            AsyncMock(return_value={"ok": True, "idle": True}),
        ),
        patch(
            "web_tab.operator_guard.read_operator_busy",
            AsyncMock(
                return_value={
                    "document_has_focus": True,
                    "document_visibility": "visible",
                    "operator_active": False,
                    "foreign_draft": False,
                }
            ),
        ),
    ):
        out = await operator_active_probe(
            "ws://fake",
            selector="[contenteditable='true']",
            created_by_agent=False,
        )
    assert out["ok"] is False
    assert out["code"] == "operator_active"


@pytest.mark.asyncio
async def test_focus_allowed_when_created_by_agent() -> None:
    with (
        patch(
            "web_tab.operator_guard.install_operator_activity_probe",
            AsyncMock(),
        ),
        patch(
            "web_tab.operator_guard.wait_for_operator_idle",
            AsyncMock(return_value={"ok": True, "idle": True}),
        ),
        patch(
            "web_tab.operator_guard.read_operator_busy",
            AsyncMock(
                return_value={
                    "document_has_focus": True,
                    "document_visibility": "visible",
                    "operator_active": False,
                    "foreign_draft": False,
                }
            ),
        ),
    ):
        out = await operator_active_probe(
            "ws://fake",
            selector="[contenteditable='true']",
            created_by_agent=True,
        )
    assert out["ok"] is True
