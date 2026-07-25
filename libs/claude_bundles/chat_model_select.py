"""claude.ai model picker helpers for CDP sealed-ask / ralph scripts.

Operator bind (a24691 / friction a24692): the live CDP model picker UI is the
sole SOT for available models. ``select_model`` may try a predicted label list
first (fast path — not an availability gate); on miss it discovers radios
(including under "More models") and matches the requested name/pattern.

Effort High/Extra/Max live under ``effort-menu-trigger`` → ``effort-option-*``
(friction 24592). Sealed-ask default for Opus/Fable is Effort **High** (operator
2026-07-16); request ``opus-5-extra`` when Extra is required.

Cowork Project nests some models under "More models" and mounts the picker
only after chat composer chrome is live.
"""

from __future__ import annotations

import re

from claude_bundles.chat_model_match import (
    PREDICTED_MODEL_LABELS,
    family_pattern,
    is_leave_request,
    label_satisfies_request,
    match_model_request,
    parse_model_request,
    sealed_ask_default_effort,
)

# Re-export pure helpers for existing callers / tests.
__all__ = [
    "PREDICTED_MODEL_LABELS",
    "current_model_label",
    "family_pattern",
    "label_satisfies_request",
    "list_picker_radios",
    "match_model_request",
    "parse_model_request",
    "sealed_ask_default_effort",
    "select_fable_5",
    "select_from_ui",
    "select_haiku_45",
    "select_model",
    "select_opus_5",
    "set_effort",
]

_EFFORT_TESTIDS = {
    "low": "effort-option-low",
    "medium": "effort-option-medium",
    "high": "effort-option-high",
    "extra": "effort-option-xhigh",
    "xhigh": "effort-option-xhigh",
    "max": "effort-option-max",
}


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
    """Cowork Project nests some models under More models."""
    more = page.locator("[role=menuitem]").filter(
        has_text=re.compile(r"More models", re.I)
    )
    if await more.count():
        await more.first.click(force=True)
        await page.wait_for_timeout(1200)


async def list_picker_radios(page) -> list[str]:
    """Return live menuitemradio labels from the open picker (UI SOT)."""
    labels = await page.evaluate(
        """() => [...document.querySelectorAll('[role=menuitemradio]')]
            .map(el => (el.getAttribute('aria-label') || el.textContent || '').trim())
            .filter(Boolean)"""
    )
    return [str(x) for x in (labels or []) if str(x).strip()]


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


async def _click_family_radio(page, family: str) -> str | None:
    """Click a family radio by pattern. Return matched label hint or None."""
    pat = family_pattern(family)
    item = await _find_radio(page, pat.pattern)
    if not await item.count():
        item = page.get_by_text(pat)
    if not await item.count():
        return None
    text = ""
    try:
        text = (await item.first.inner_text() or "").strip()
    except Exception:
        text = family
    await item.first.click(force=True)
    await page.wait_for_timeout(800)
    return text or family


async def _apply_effort(page, effort: str, *, matched: str | None) -> dict:
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


async def _discover_and_click(
    page,
    *,
    family: str,
    before: str,
    requested: str,
    predicted: str | None,
) -> tuple[str | None, list[str], dict | None]:
    """Live UI SOT path. Returns (matched, available, error_dict|None)."""
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)
    await _open_picker(page)
    await _expand_more_models(page)
    available = await list_picker_radios(page)
    matched = match_model_request(family, available)
    if not matched:
        return (
            None,
            available,
            {
                "ok": False,
                "step": "model_not_in_ui",
                "before": before,
                "requested": requested,
                "family": family,
                "available_models": available,
                "predicted": predicted,
            },
        )
    if await _click_family_radio(page, family) is None:
        return (
            matched,
            available,
            {
                "ok": False,
                "step": "model_radio_unclickable",
                "before": before,
                "matched": matched,
                "available_models": available,
            },
        )
    return matched, available, None


async def select_from_ui(
    page,
    requested: str,
    *,
    effort: str | None = None,
) -> dict:
    """Select via prediction fast-path, then live UI discovery on miss."""
    family, parsed_effort = parse_model_request(requested)
    if effort is None:
        effort = parsed_effort
    before = await current_model_label(page)
    if label_satisfies_request(requested, before, effort=effort):
        return {
            "ok": True,
            "step": "already_selected",
            "current_model": before,
            "requested": requested,
        }

    if not await _ensure_picker(page):
        return {"ok": False, "step": "no_picker", "before": before, "requested": requested}

    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)
    await _open_picker(page)

    predicted = match_model_request(family, list(PREDICTED_MODEL_LABELS))
    path = "discover"
    matched: str | None = None
    available: list[str] = []

    if predicted:
        matched = await _click_family_radio(page, family)
        if matched is None:
            await _expand_more_models(page)
            matched = await _click_family_radio(page, family)
        if matched is not None:
            path = "predicted"
            matched = predicted

    if path != "predicted":
        matched, available, err = await _discover_and_click(
            page,
            family=family,
            before=before,
            requested=requested,
            predicted=predicted,
        )
        if err is not None:
            return err

    effort_result = None
    if effort:
        applied = await _apply_effort(page, effort, matched=matched)
        if not applied.get("ok"):
            applied["before"] = before
            return applied
        effort_result = applied.get("effort")

    after = await current_model_label(page)
    if not label_satisfies_request(requested, after, effort=effort):
        if path == "predicted":
            matched, available, err = await _discover_and_click(
                page,
                family=family,
                before=before,
                requested=requested,
                predicted=predicted,
            )
            if err is not None:
                return err
            path = "discover_after_predict_miss"
            effort_result = None
            if effort:
                applied = await _apply_effort(page, effort, matched=matched)
                if not applied.get("ok"):
                    applied["before"] = before
                    return applied
                effort_result = applied.get("effort")
            after = await current_model_label(page)
            if label_satisfies_request(requested, after, effort=effort):
                return {
                    "ok": True,
                    "step": path,
                    "current_model": after,
                    "matched": matched,
                    "requested": requested,
                    "family": family,
                    "effort": effort_result,
                    "available_models": available,
                }
        return {
            "ok": False,
            "step": "select_no_attest",
            "before": before,
            "after": after,
            "matched": matched,
            "effort": effort_result,
            "available_models": available,
            "path": path,
        }
    return {
        "ok": True,
        "step": path,
        "current_model": after,
        "matched": matched,
        "requested": requested,
        "family": family,
        "effort": effort_result,
        "available_models": available,
    }


async def select_opus_5(page, *, prefer_extra: bool = False) -> dict:
    """Ensure Opus 5; default Effort High (``prefer_extra=True`` → Extra)."""
    effort = "extra" if prefer_extra else "high"
    return await select_from_ui(page, "opus-5", effort=effort)


async def select_fable_5(page) -> dict:
    """Ensure Fable 5 for protocol consult seat."""
    return await select_model(page, "fable-5")


async def select_haiku_45(page) -> dict:
    """Ensure Haiku 4.5 for fast spoken-voice / fluidity passes."""
    return await select_from_ui(page, "haiku-4.5", effort=None)


async def select_model(page, model: str) -> dict:
    """Select by prediction then live UI discovery; or leave.

    Examples: ``opus-5``, ``fable-5``, ``sonnet-5``, ``haiku-4.5``, ``leave``.
    ``PREDICTED_MODEL_LABELS`` is try-first only — availability SOT remains the picker.
    """
    key = (model or "opus-5").strip().lower()
    if is_leave_request(key):
        label = await current_model_label(page)
        return {"ok": True, "step": "leave", "current_model": label}
    family, effort = parse_model_request(model or "opus-5")
    if effort is None:
        effort = sealed_ask_default_effort(family)
    return await select_from_ui(page, model or "opus-5", effort=effort)
