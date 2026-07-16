"""claude.ai model picker helpers for CDP sealed-ask / ralph scripts.

Lane binding:
- Opus 4.8 + Effort Extra (default): sealed-ask / plan seats
- Opus 4.8 High: disposable micro-asks when Extra not required
- Fable 5 High: protocol consult only (when intentionally selected)

Family radios are model-only (``Opus 4.8``); High/Extra/Max live under
``effort-menu-trigger`` → ``effort-option-*`` (friction 24592, CDP :9223).

Cowork Project nests some models under "More models" and mounts the picker
only after chat composer chrome is live.
"""

from __future__ import annotations

import re


async def current_model_label(page) -> str:
    btn = page.locator('[data-testid="model-selector-dropdown"]')
    if not await btn.count():
        return ""
    return (
        await btn.first.get_attribute("aria-label") or await btn.first.inner_text()
    ).strip()


async def _ensure_picker(page) -> bool:
    """Focus composer and wait until model dropdown exists. Return True if found."""
    btn = page.locator('[data-testid="model-selector-dropdown"]')
    if await btn.count():
        return True
    composer = page.locator('[data-testid="chat-input"]')
    for _ in range(12):
        if await composer.count():
            try:
                await composer.first.click(force=True)
            except Exception:
                pass
        await page.wait_for_timeout(500)
        if await btn.count():
            return True
    return await btn.count() > 0


async def _open_picker(page) -> None:
    btn = page.locator('[data-testid="model-selector-dropdown"]')
    if await btn.count():
        await btn.first.click(force=True)
        await page.wait_for_timeout(1200)


async def _expand_more_models(page) -> None:
    """Cowork Project nests Fable (and sometimes others) under More models."""
    more = page.locator("[role=menuitem]").filter(
        has_text=re.compile(r"More models", re.I)
    )
    if await more.count():
        await more.first.click(force=True)
        await page.wait_for_timeout(1200)


# Effort submenu under model picker (Jupiter CDP :9223 probe 2026-07-16).
# Family radios are "Opus 4.8" only; Extra/Max are effort-option-* siblings.
_EFFORT_TESTIDS = {
    "low": "effort-option-low",
    "medium": "effort-option-medium",
    "high": "effort-option-high",
    "extra": "effort-option-xhigh",
    "xhigh": "effort-option-xhigh",
    "max": "effort-option-max",
}


async def _find_radio(page, pattern: str):
    item = page.locator("[role=menuitemradio]").filter(
        has_text=re.compile(pattern, re.I)
    )
    if await item.count():
        return item
    return page.get_by_role("menuitemradio", name=re.compile(pattern, re.I))


async def set_effort(page, level: str) -> dict:
    """Set Thinking Effort via model-picker submenu (High / Extra / Max / …).

    Requires the model picker menu to already be open. ``level`` is one of
    low|medium|high|extra|xhigh|max.
    """
    key = (level or "").strip().lower()
    testid = _EFFORT_TESTIDS.get(key)
    if not testid:
        return {"ok": False, "step": "effort_unknown", "requested": level}

    trigger = page.locator('[data-testid="effort-menu-trigger"]')
    if not await trigger.count():
        return {"ok": False, "step": "effort_trigger_missing"}
    await trigger.first.click(force=True)
    await page.wait_for_timeout(600)

    opt = page.locator(f'[data-testid="{testid}"]')
    if not await opt.count():
        return {"ok": False, "step": "effort_option_missing", "testid": testid}
    await opt.first.click(force=True)
    await page.wait_for_timeout(800)
    return {"ok": True, "step": "effort_set", "level": key, "testid": testid}


async def select_opus_48(page, *, prefer_extra: bool = True) -> dict:
    """Ensure Opus 4.8 for sealed-ask / plan seats.

    When ``prefer_extra`` is True, select family Opus 4.8 then Effort→Extra
    (``effort-option-xhigh``). Attestation requires ``Extra`` in the model
    label — High must not silently pass.
    """
    before = await current_model_label(page)
    if prefer_extra and re.search(r"Opus\s*4\.8.*Extra", before, re.I):
        return {"ok": True, "step": "already_opus_extra", "current_model": before}
    if (not prefer_extra) and re.search(r"Opus\s*4\.8", before, re.I):
        return {"ok": True, "step": "already_opus", "current_model": before}

    if not await _ensure_picker(page):
        return {"ok": False, "step": "opus_no_picker", "before": before}

    # Close any open menu first (re-click toggles).
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)
    await _open_picker(page)

    item = await _find_radio(page, r"Opus\s*4\.8")
    if not await item.count():
        await _expand_more_models(page)
        item = await _find_radio(page, r"Opus\s*4\.8")
    if not await item.count():
        item = page.get_by_text(re.compile(r"^Opus\s*4\.8", re.I))
    if not await item.count():
        return {"ok": False, "step": "opus_not_found", "before": before}
    await item.first.click(force=True)
    await page.wait_for_timeout(800)

    effort = None
    if prefer_extra:
        # Family click may close the menu — reopen before Effort submenu.
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
        await _open_picker(page)
        effort = await set_effort(page, "extra")
        if not effort.get("ok"):
            after = await current_model_label(page)
            return {
                "ok": False,
                "step": "opus_effort_extra_failed",
                "before": before,
                "after": after,
                "effort": effort,
            }
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)

    after = await current_model_label(page)
    if not re.search(r"Opus\s*4\.8", after, re.I):
        return {
            "ok": False,
            "step": "opus_select_no_attest",
            "before": before,
            "after": after,
            "effort": effort,
        }
    if prefer_extra and not re.search(r"Extra", after, re.I):
        return {
            "ok": False,
            "step": "opus_extra_no_attest",
            "before": before,
            "after": after,
            "effort": effort,
        }
    return {
        "ok": True,
        "current_model": after,
        "matched_pattern": "Opus\\s*4\\.8",
        "effort": effort,
    }


async def select_fable_5(page) -> dict:
    """Ensure Fable 5 for protocol consult seat."""
    before = await current_model_label(page)
    if re.search(r"fable\s*5", before, re.I):
        return {"ok": True, "step": "already_fable", "current_model": before}

    if not await _ensure_picker(page):
        return {"ok": False, "step": "fable_no_picker", "before": before}

    await _open_picker(page)
    item = await _find_radio(page, r"Fable\s*5")
    if not await item.count():
        await _expand_more_models(page)
        item = await _find_radio(page, r"Fable\s*5")
    if not await item.count():
        return {"ok": False, "step": "fable_not_found", "before": before}
    await item.first.click(force=True)
    await page.wait_for_timeout(1500)
    after = await current_model_label(page)
    if not re.search(r"fable\s*5", after, re.I):
        return {
            "ok": False,
            "step": "fable_select_no_attest",
            "before": before,
            "after": after,
        }
    return {"ok": True, "current_model": after}


async def select_haiku_45(page) -> dict:
    """Ensure Haiku 4.5 for fast spoken-voice / fluidity passes."""
    before = await current_model_label(page)
    if re.search(r"Haiku\s*4\.5", before, re.I):
        return {"ok": True, "step": "already_haiku", "current_model": before}

    if not await _ensure_picker(page):
        return {"ok": False, "step": "haiku_no_picker", "before": before}

    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)
    await _open_picker(page)
    item = await _find_radio(page, r"Haiku\s*4\.5")
    if not await item.count():
        await _expand_more_models(page)
        item = await _find_radio(page, r"Haiku\s*4\.5")
    if not await item.count():
        item = page.get_by_text(re.compile(r"^Haiku\s*4\.5", re.I))
    if not await item.count():
        return {"ok": False, "step": "haiku_not_found", "before": before}
    await item.first.click(force=True)
    await page.wait_for_timeout(1500)
    after = await current_model_label(page)
    if not re.search(r"Haiku\s*4\.5", after, re.I):
        return {
            "ok": False,
            "step": "haiku_select_no_attest",
            "before": before,
            "after": after,
        }
    return {"ok": True, "current_model": after}


async def select_model(page, model: str) -> dict:
    """Dispatch by short name: opus-4.8 | fable-5 | haiku-4.5 | leave."""
    key = (model or "opus-4.8").strip().lower()
    if key in ("leave", "none", "current"):
        label = await current_model_label(page)
        return {"ok": True, "step": "leave", "current_model": label}
    if key in (
        "opus",
        "opus-4.8",
        "opus4.8",
        "opus_4_8",
        "opus-extra",
        "opus-4.8-extra",
    ):
        return await select_opus_48(page, prefer_extra=True)

    if key in ("fable", "fable-5", "fable5", "fable_5"):
        return await select_fable_5(page)
    if key in ("haiku", "haiku-4.5", "haiku4.5", "haiku_4_5"):
        return await select_haiku_45(page)
    return {"ok": False, "step": "unknown_model", "requested": model}
