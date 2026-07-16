"""claude.ai model picker helpers for CDP sealed-ask / ralph scripts.

Lane binding:
- Opus 4.8 High: disposable subagent asks (critique / apply / micro-ask)
- Fable 5 High: protocol consult only (when intentionally selected)

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


async def _find_radio(page, pattern: str):
    item = page.locator("[role=menuitemradio]").filter(
        has_text=re.compile(pattern, re.I)
    )
    if await item.count():
        return item
    return page.get_by_role("menuitemradio", name=re.compile(pattern, re.I))


async def select_opus_48(page) -> dict:
    """Ensure Opus 4.8 for sealed-ask seats."""
    before = await current_model_label(page)
    if re.search(r"Opus\s*4\.8", before, re.I):
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
        # Last resort: plain text match in open menu.
        item = page.get_by_text(re.compile(r"^Opus\s*4\.8", re.I))
    if not await item.count():
        return {"ok": False, "step": "opus_not_found", "before": before}
    await item.first.click(force=True)
    await page.wait_for_timeout(1500)
    after = await current_model_label(page)
    if not re.search(r"Opus\s*4\.8", after, re.I):
        return {
            "ok": False,
            "step": "opus_select_no_attest",
            "before": before,
            "after": after,
        }
    return {"ok": True, "current_model": after}


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
    if key in ("opus", "opus-4.8", "opus4.8", "opus_4_8"):
        return await select_opus_48(page)
    if key in ("fable", "fable-5", "fable5", "fable_5"):
        return await select_fable_5(page)
    if key in ("haiku", "haiku-4.5", "haiku4.5", "haiku_4_5"):
        return await select_haiku_45(page)
    return {"ok": False, "step": "unknown_model", "requested": model}
