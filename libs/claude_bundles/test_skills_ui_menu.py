"""Offline tests for Add → Upload menu inventory and preflight gate."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_bundles.skills_ui_menu import (
    MenuInventory,
    MenuItem,
    MenuPopup,
    MenuTrigger,
    PreflightMenuError,
    UploadSelection,
    assert_preflight_selection,
    inventory_from_raw,
    select_upload_item,
)
from claude_bundles.skills_ui_panel import run_preflight


def _item(
    index: int,
    text: str,
    *,
    visible: bool = True,
    aria_haspopup: str = "",
    aria_disabled: str = "",
    item_id: str = "",
) -> MenuItem:
    return MenuItem(
        index=index,
        text=text,
        role="menuitem",
        aria_haspopup=aria_haspopup,
        aria_disabled=aria_disabled,
        visible=visible,
        id=item_id,
    )


def _inv(
    *items: MenuItem,
    mounted: bool = True,
    found_by: str = "aria-controls",
    url: str = "https://claude.ai/new#settings/customize-skills",
) -> MenuInventory:
    return MenuInventory(
        url=url,
        captured_at="2026-08-25T00:00:00+00:00",
        trigger=MenuTrigger(
            found=True,
            aria_haspopup="menu",
            aria_expanded="true",
            aria_controls="menu-1",
        ),
        popup=MenuPopup(
            found_by=found_by,
            id="menu-1",
            mounted=mounted,
            visible=mounted,
            menuitem_count=len(items),
        ),
        items=list(items),
    )


def test_canonical_label_selects_index() -> None:
    inv = _inv(_item(0, "Create a skill"), _item(1, "Upload a skill"))
    sel = select_upload_item(inv)
    assert sel.status == "found"
    assert sel.index == 1
    assert sel.drift is False


def test_label_drift_single_upload_candidate() -> None:
    inv = _inv(_item(0, "Browse connectors"), _item(1, "Upload skill zip"))
    sel = select_upload_item(inv)
    assert sel.status == "drift"
    assert sel.index == 1
    assert sel.drift is True


def test_two_upload_candidates_refuses_guess() -> None:
    inv = _inv(_item(0, "Upload zip"), _item(1, "Upload from folder"))
    sel = select_upload_item(inv)
    assert sel.status == "ambiguous"
    assert sel.index is None
    assert sel.reason == "multiple_upload"


def test_nested_submenu_without_upload() -> None:
    inv = _inv(
        _item(0, "More actions", aria_haspopup="menu"),
        _item(1, "Browse connectors"),
    )
    sel = select_upload_item(inv)
    assert sel.status == "needs_submenu"
    assert sel.index == 0


def test_aria_controls_missing_uses_role_fallback_inventory() -> None:
    raw = {
        "trigger": {
            "found": True,
            "aria_controls": "",
            "aria_haspopup": "menu",
            "aria_expanded": "true",
            "data_popup_open": True,
        },
        "popup": {
            "found_by": "role-menu",
            "id": "",
            "mounted": True,
            "visible": True,
            "menuitem_count": 1,
        },
        "items": [
            {
                "index": 0,
                "text": "Upload a skill",
                "role": "menuitem",
                "aria_haspopup": "",
                "aria_disabled": "",
                "visible": True,
                "id": "",
            }
        ],
    }
    inv = inventory_from_raw(raw, "https://claude.ai/new#settings/customize-skills")
    assert inv.popup.found_by == "role-menu"
    sel = select_upload_item(inv)
    assert sel.status == "found"
    assert sel.index == 0


def test_portal_not_mounted_is_none() -> None:
    inv = _inv(mounted=False)
    sel = select_upload_item(inv)
    assert sel.status == "none"
    assert sel.reason == "portal_not_mounted"


def test_assert_preflight_selection_raises_on_unmounted() -> None:
    inv = _inv(mounted=False)
    with pytest.raises(PreflightMenuError) as ei:
        assert_preflight_selection(inv)
    assert ei.value.inventory is inv
    assert "portal_not_mounted" in str(ei.value) or "not selectable" in str(ei.value)


@pytest.mark.asyncio
async def test_run_preflight_raises_preflight_menu_error_with_inventory() -> None:
    inv = _inv(mounted=False)
    page = AsyncMock()
    page.url = "https://claude.ai/new#settings/customize-skills"
    context = MagicMock()
    add = AsyncMock()
    with (
        patch(
            "claude_bundles.skills_ui_panel._page_blocked",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "claude_bundles.skills_ui_panel.open_skills_panel",
            new_callable=AsyncMock,
            return_value=page,
        ),
        patch(
            "claude_bundles.skills_ui_panel._find_add_button",
            new_callable=AsyncMock,
            return_value=add,
        ),
        patch(
            "claude_bundles.skills_ui_panel.stability_guarded_add_click",
            new_callable=AsyncMock,
        ),
        patch(
            "claude_bundles.skills_ui_panel.assert_add_menu_upload_ready",
            new_callable=AsyncMock,
            side_effect=PreflightMenuError("portal_not_mounted", inv),
        ),
        patch(
            "claude_bundles.skills_ui_panel.panel_state_summary",
            new_callable=AsyncMock,
            return_value="panel=True add=True rows=49",
        ),
    ):
        with pytest.raises(PreflightMenuError) as ei:
            await run_preflight(page, context)
    assert ei.value.inventory is inv
    assert "panel=True add=True rows=49" in str(ei.value)


def test_assert_preflight_allows_found_and_submenu() -> None:
    found = _inv(_item(0, "Upload a skill"))
    assert assert_preflight_selection(found).status == "found"
    nested = _inv(_item(0, "More", aria_haspopup="menu"))
    assert assert_preflight_selection(nested).status == "needs_submenu"


def test_select_ignores_hidden_and_disabled() -> None:
    inv = _inv(
        _item(0, "Upload a skill", visible=False),
        _item(1, "Upload a skill", aria_disabled="true"),
        _item(2, "Create a skill"),
    )
    sel = select_upload_item(inv)
    assert sel.status == "none"
    assert sel.reason == "no_upload_item"


def test_upload_selection_dataclass_defaults() -> None:
    sel = UploadSelection(status="none", reason="portal_not_mounted")
    assert sel.index is None
    assert sel.drift is False


def test_diagnose_payload_contract_keys() -> None:
    from claude_bundles.skills_ui_menu import diagnose_payload

    inv = _inv(_item(0, "Upload a skill"))
    sel = select_upload_item(inv)
    payload = diagnose_payload(
        inv,
        panel_visible=True,
        rows=49,
        composer_chips=False,
        selection=sel,
        evidence_paths={"screenshot": "/tmp/x.png"},
    )
    assert payload["panel_visible"] is True
    assert payload["rows"] == 49
    assert payload["selection"]["status"] == "found"
    assert payload["selection"]["index"] == 0
    assert payload["composer_chips"] is False
    assert payload["evidence_paths"]["screenshot"] == "/tmp/x.png"
    assert payload["trigger"]["aria_haspopup"] == "menu"
    assert payload["popup"]["found_by"] == "aria-controls"
    assert payload["items"][0]["text"] == "Upload a skill"
