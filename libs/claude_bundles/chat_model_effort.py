"""Playwright effort submenu helpers and Cowork qualified-radio recovery for CDP picker.

Callers include ``chat_model_select.select_from_ui`` and ``_effort_after_click``.
Invariant: ``set_effort`` stays submenu-only and never clicks Cowork effort-qualified radios.
"""

from __future__ import annotations

import re

from effort_vocabulary import to_picker_label as _effort_label
from effort_vocabulary import to_testid as _effort_testid

from claude_bundles.chat_model_match import (
    family_nested_in_more_models,
    label_satisfies_request,
    match_effort_qualified_radio,
    parse_model_request,
)

_EFFORT_ROW = re.compile(r"\bEffort\b", re.I)


async def _count(locator) -> int:
    return int(await locator.count()) if locator is not None else 0


async def _effort_trigger(page):
    """Legacy testid, else the Cowork/Chat ``Effort`` menuitem (a:31119)."""
    testid = page.locator('[data-testid="effort-menu-trigger"]')
    if await _count(testid):
        return testid, "testid"
    row = page.locator("[role=menuitem]").filter(has_text=_EFFORT_ROW)
    if await _count(row):
        return row, "menuitem"
    named = page.get_by_role("menuitem", name=_EFFORT_ROW)
    if await _count(named):
        return named, "menuitem-name"
    return None, None


async def _effort_option(page, key: str, testid: str):
    """Legacy ``effort-option-*``, else flyout label Low/Medium/High/Extra/Max."""
    by_id = page.locator(f'[data-testid="{testid}"]')
    if await _count(by_id):
        return by_id, "testid"
    label = _effort_label(key)
    if not label:
        return None, None
    pat = re.compile(rf"^{re.escape(label)}\b", re.I)
    radio = page.locator("[role=menuitemradio]").filter(has_text=pat)
    if await _count(radio):
        return radio, "label-radio"
    item = page.locator("[role=menuitem]").filter(has_text=pat)
    if await _count(item):
        return item, "label-item"
    named = page.get_by_role("menuitem", name=pat)
    if await _count(named):
        return named, "label-name"
    return None, None


async def set_effort(page, level: str) -> dict:
    """Set Thinking Effort via the open model-picker submenu.

    Prefers ``effort-menu-trigger`` / ``effort-option-*``. On miss, clicks the
    visible ``Effort`` row then Low/Medium/High/Extra/Max (Chat and Cowork).
    Does not click family radios; callers still recover via qualified radio.
    """
    key = (level or "").strip().lower()
    testid = _effort_testid(key)
    if not testid:
        return {"ok": False, "step": "effort_unknown", "requested": level}

    trigger, trigger_via = await _effort_trigger(page)
    if trigger is None:
        return {"ok": False, "step": "effort_trigger_missing"}
    await trigger.first.click(force=True)
    await page.wait_for_timeout(600)

    opt, option_via = await _effort_option(page, key, testid)
    if opt is None:
        return {"ok": False, "step": "effort_option_missing", "testid": testid}
    await opt.first.click(force=True)
    await page.wait_for_timeout(800)
    return {
        "ok": True,
        "step": "effort_set",
        "level": key,
        "testid": testid,
        "trigger_via": trigger_via,
        "option_via": option_via,
    }


async def _apply_effort(page, effort: str, *, matched: str | None) -> dict:
    from claude_bundles.chat_model_select import _open_picker, current_model_label

    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)
    await _open_picker(page)
    effort_result = await set_effort(page, effort)
    if not effort_result.get("ok"):
        after = await current_model_label(page)
        return {
            "ok": False,
            "step": "effort_failed",
            "after": after,
            "effort": effort_result,
            "matched": matched,
        }
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)
    return {"ok": True, "effort": effort_result}


async def _recover_effort_via_qualified_radio(
    page,
    *,
    family: str,
    effort: str,
    requested: str,
    before: str,
    matched: str | None,
) -> dict:
    from claude_bundles.chat_model_select import (
        _click_radio_named,
        _expand_more_models,
        _open_picker,
        current_model_label,
        list_picker_radios,
    )

    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)
    await _open_picker(page)
    if family_nested_in_more_models(family):
        await _expand_more_models(page)
    available = await list_picker_radios(page)
    qualified = match_effort_qualified_radio(family, available, effort=effort)
    if not qualified or await _click_radio_named(page, qualified) is None:
        return {
            "ok": False,
            "step": "effort_failed",
            "after": await current_model_label(page),
            "effort": {"ok": False, "step": "effort_trigger_missing"},
            "matched": matched,
            "before": before,
            "available_models": available,
        }
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)
    after = await current_model_label(page)
    if label_satisfies_request(requested, after, effort=effort):
        return {
            "ok": True,
            "step": "recovered_via_qualified_radio",
            "effort": {
                "ok": True,
                "step": "effort_set_via_qualified_radio",
                "level": effort,
            },
            "current_model": after,
            "matched": qualified,
            "available_models": available,
        }
    return {
        "ok": False,
        "step": "effort_failed",
        "after": after,
        "effort": {"ok": False, "step": "effort_trigger_missing"},
        "matched": matched,
        "before": before,
        "available_models": available,
    }


async def _effort_after_click(
    page,
    *,
    requested: str,
    effort: str | None,
    matched: str | None,
    before: str,
) -> tuple[dict | None, dict | None]:
    """Skip the effort submenu when the dropdown already attests it (a:30693)."""
    from claude_bundles.chat_model_select import current_model_label

    if not effort:
        return None, None
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)
    after_click = await current_model_label(page)
    if label_satisfies_request(requested, after_click, effort=effort):
        return (
            {
                "ok": True,
                "step": "effort_already_on_label",
                "level": effort,
                "current_model": after_click,
            },
            None,
        )
    applied = await _apply_effort(page, effort, matched=matched)
    if not applied.get("ok"):
        effort_step = applied.get("effort", {}).get("step")
        if effort_step in {"effort_trigger_missing", "effort_option_missing"}:
            family, _ = parse_model_request(requested)
            recover = await _recover_effort_via_qualified_radio(
                page,
                family=family,
                effort=effort,
                requested=requested,
                before=before,
                matched=matched,
            )
            if recover.get("ok"):
                return recover, None
            return None, recover
        applied["before"] = before
        return None, applied
    return applied.get("effort"), None
