"""claude.ai model picker helpers for CDP sealed-ask / ralph scripts.

Operator bind (a24691 / friction a24692): the live CDP model picker UI is the
sole SOT for available models. ``select_model`` may try a predicted label list
first (fast path — not an availability gate); on miss it discovers radios
(including under "More models") and matches the requested name/pattern.

Effort High/Extra/Max may be first-class radios (click ``Opus 5 High``
directly — a:30693), ``effort-menu-trigger`` → ``effort-option-*`` (24592),
or the Chat/Cowork ``Effort`` flyout row (a:31119). Skip submenu when the
label attests effort; on trigger/option miss recover via effort-qualified
radio (a:31011). Sealed-ask default for Opus/Fable is **High**;
request ``opus-5-extra`` when Extra is required.

Cowork Project nests some models under "More models" and mounts the picker
only after chat composer chrome is live.
"""

from __future__ import annotations

import re

from claude_bundles.chat_model_effort import (
    _effort_after_click,
    set_effort,
)
from claude_bundles.chat_model_match import (
    PREDICTED_MODEL_LABELS,
    family_attested,
    family_nested_in_more_models,
    family_pattern,
    index_for_named_radio,
    is_leave_request,
    label_satisfies_request,
    match_effort_qualified_radio,
    match_model_request,
    normalize_picker_request,
    parse_model_request,
    prefer_model_name_index,
    sealed_ask_default_effort,
    select_no_attest_status,
)

# Re-export pure helpers for existing callers / tests.
__all__ = [
    "PREDICTED_MODEL_LABELS",
    "current_model_label",
    "family_pattern",
    "label_satisfies_request",
    "list_picker_radios",
    "match_effort_qualified_radio",
    "match_model_request",
    "normalize_picker_request",
    "parse_model_request",
    "picker_attests_request",
    "sealed_ask_default_effort",
    "select_fable_5_1",
    "select_from_ui",
    "select_haiku_45",
    "select_model",
    "select_opus_5",
    "set_effort",
]


async def current_model_label(page) -> str:
    """Read the live model-selector dropdown label (aria-label or inner text)."""
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
    """Open the model dropdown; retry until radios mount (empty /new race)."""
    btn = page.locator('[data-testid="model-selector-dropdown"]')
    if not await btn.count():
        return
    for attempt in range(6):
        await btn.first.click(force=True)
        await page.wait_for_timeout(1200 if attempt == 0 else 800)
        if await list_picker_radios(page):
            return
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)


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


# Own label is the first line, with nested radios removed. ``textContent``
# glues a subtitle onto the model name and a parent group concatenates rows.
# ``el.click()`` fires the radio; a coordinate click on ``locator.first``
# lands on the already-selected row's effort control.
_LIST_RADIOS_JS = """() => {
  function ownLabel(el) {
    const aria = (el.getAttribute('aria-label') || '').trim();
    if (aria) return aria;
    const clone = el.cloneNode(true);
    clone.querySelectorAll('[role="menuitemradio"]').forEach((n) => n.remove());
    const raw = (clone.innerText || clone.textContent || '').trim();
    return raw.split(/\\n/).map((s) => s.trim()).filter(Boolean)[0] || '';
  }
  return [...document.querySelectorAll('[role=menuitemradio]')].map((el) => ({
    own: ownLabel(el),
    full: ((el.getAttribute('aria-label') || el.textContent || '')).trim(),
  }));
}"""

_CLICK_INDEX_JS = """(index) => {
  const radios = [...document.querySelectorAll('[role=menuitemradio]')];
  const el = radios[index];
  if (!el) return false;
  el.click();
  return true;
}"""


async def _radio_rows(page) -> list[dict[str, str]]:
    raw = await page.evaluate(_LIST_RADIOS_JS)
    rows: list[dict[str, str]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "own": str(item.get("own") or "").strip(),
                "full": str(item.get("full") or "").strip(),
            }
        )
    return rows


async def _click_radio_index(page, index: int) -> bool:
    clicked = await page.evaluate(_CLICK_INDEX_JS, index)
    if clicked is not True:
        return False
    await page.wait_for_timeout(800)
    return True


async def _click_family_radio(page, family: str) -> str | None:
    """Click the radio whose own label is the model name.

    ``locator.first`` on a family substring matches the parent group first.
    That click changes effort on the selected model and leaves the chip.
    """
    rows = await _radio_rows(page)
    index = prefer_model_name_index(family, rows)
    if index is None or not await _click_radio_index(page, index):
        return None
    return rows[index]["own"] or family


async def _click_radio_named(page, label: str) -> str | None:
    """Click the radio whose own or full label equals ``label``.

    A parent row contains that label as a substring. Exact equality plus
    ``el.click()`` selects the named SKU instead of the group's center.
    """
    text = (label or "").strip()
    if not text:
        return None
    rows = await _radio_rows(page)
    index = index_for_named_radio(rows, text)
    if index is None or not await _click_radio_index(page, index):
        return None
    return text


async def _discover_and_click(
    page,
    *,
    family: str,
    before: str,
    requested: str,
    predicted: str | None,
    effort: str | None = None,
) -> tuple[str | None, list[str], dict | None]:
    """Live UI SOT path. Returns (matched, available, error_dict|None)."""
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)
    await _open_picker(page)
    await _expand_more_models(page)
    available = await list_picker_radios(page)
    qualified = match_effort_qualified_radio(family, available, effort=effort)
    if qualified and await _click_radio_named(page, qualified) is not None:
        return qualified, available, None
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
    clicked = await _click_family_radio(page, family)
    if clicked is None:
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
    return clicked, available, None


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
        return {
            "ok": False,
            "step": "no_picker",
            "before": before,
            "requested": requested,
        }

    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)
    await _open_picker(page)
    if family_nested_in_more_models(family):
        await _expand_more_models(page)

    predicted = match_model_request(family, list(PREDICTED_MODEL_LABELS))
    path = "discover"
    matched: str | None = None
    available: list[str] = await list_picker_radios(page)
    qualified = match_effort_qualified_radio(family, available, effort=effort)
    if qualified:
        clicked = await _click_radio_named(page, qualified)
        if clicked is not None:
            path = "effort_qualified_radio"
            matched = qualified

    if (
        path == "discover"
        and predicted
        and not (
            effort and not label_satisfies_request(requested, before, effort=effort)
        )
    ):
        matched = await _click_family_radio(page, family)
        if matched is None:
            await _expand_more_models(page)
            matched = await _click_family_radio(page, family)
        if matched is not None:
            path = "predicted"
            matched = predicted

    if path == "discover":
        matched, available, err = await _discover_and_click(
            page,
            family=family,
            before=before,
            requested=requested,
            predicted=predicted,
            effort=effort,
        )
        if err is not None:
            return err

    after_click = await current_model_label(page)
    if not family_attested(requested, after_click) and path in {
        "predicted",
        "effort_qualified_radio",
    }:
        matched, available, err = await _discover_and_click(
            page,
            family=family,
            before=before,
            requested=requested,
            predicted=predicted,
            effort=effort,
        )
        if err is not None:
            return err
        path = "discover_after_predict_miss"
        after_click = await current_model_label(page)

    if not family_attested(requested, after_click):
        return select_no_attest_status(
            requested=requested,
            before=before,
            after=after_click,
            matched=matched,
            path=path,
            available=available,
        )

    effort_result, effort_err = await _effort_after_click(
        page,
        requested=requested,
        effort=effort,
        matched=matched,
        before=before,
    )
    if effort_err is not None:
        return effort_err

    after = await current_model_label(page)
    if not label_satisfies_request(requested, after, effort=effort):
        return select_no_attest_status(
            requested=requested,
            before=before,
            after=after,
            matched=matched,
            path=path,
            available=available,
            effort=effort_result if isinstance(effort_result, dict) else None,
        )
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


async def select_fable_5_1(page) -> dict:
    """Ensure Fable 5.1 for protocol consult seat."""
    return await select_model(page, "fable-5.1")


async def picker_attests_request(page, model: str) -> bool:
    """True when the live picker label satisfies ``model`` (post-select verify)."""
    wire = normalize_picker_request(model)
    family, effort = parse_model_request(wire)
    if effort is None:
        effort = sealed_ask_default_effort(family)
    label = await current_model_label(page)
    return label_satisfies_request(wire, label, effort=effort)


async def select_haiku_45(page) -> dict:
    """Ensure Haiku 4.5 for fast spoken-voice / fluidity passes."""
    return await select_from_ui(page, "haiku-4.5", effort=None)


async def select_model(page, model: str) -> dict:
    """Select by prediction then live UI discovery; or leave.

    Examples: ``opus-5``, ``fable-5.1``, ``sonnet-5``, ``haiku-4.5``, ``leave``.
    ``PREDICTED_MODEL_LABELS`` is try-first only — availability SOT remains the picker.
    """
    wire = normalize_picker_request(model)
    key = wire.strip().lower()
    if is_leave_request(key):
        label = await current_model_label(page)
        return {"ok": True, "step": "leave", "current_model": label}
    family, effort = parse_model_request(wire)
    if effort is None:
        effort = sealed_ask_default_effort(family)
    return await select_from_ui(page, wire, effort=effort)
