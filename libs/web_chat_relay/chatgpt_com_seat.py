"""chatgpt.com model + thinking-power probe/ensure over CDP (vendor substrate, not fleet persona).

Read DOM → compare target → apply deltas → verify. Used before paste on any ChatGPT tab
delivery when ``chatgpt_seat`` / ``seat`` is present on pipeline options.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

MODEL_PICKER_SELECTOR = 'button[aria-label="Select ChatGPT model"]'

_READ_SEAT_JS = """
() => {
  const picker = document.querySelector('button[aria-label="Select ChatGPT model"]');
  const closedLabel = picker ? (picker.innerText || '').trim() : '';
  const menu = document.querySelector('[role=menu]');
  const models = menu
    ? [...menu.querySelectorAll('[role=menuitemradio]')].map(el => ({
        label: (el.innerText || '').trim().split('\\n')[0],
        checked: el.getAttribute('aria-checked') === 'true',
        disabled: el.getAttribute('aria-disabled') === 'true' || !!el.disabled,
      }))
    : [];
  const statusEl = menu ? menu.querySelector('[role=status]') : null;
  const statusText = statusEl ? (statusEl.innerText || '').trim() : '';
  const menuText = menu ? (menu.innerText || '') : '';
  const blockers = [];
  if (/usage limit reached/i.test(menuText)) blockers.push('usage_limit_reached');
  return { closedLabel, models, statusText, menuPresent: !!menu, blockers };
}
"""

_POWER_STATUS_RE = re.compile(r"^(.+?),\s*(\d+)\s+of\s+(\d+)\.?\s*$", re.I)

_POWER_ALIASES: dict[str, str] = {
    "max": "max",
    "extra_high": "max",
    "extra high": "max",
    "extrahigh": "max",
    "high": "high",
    "medium": "medium",
    "instant": "instant",
    "low": "instant",
}


@dataclass(frozen=True)
class ChatgptComSeatState:
    closed_label: str = ""
    model: str | None = None
    power_index: int | None = None
    power_total: int | None = None
    power_name: str | None = None
    models_available: tuple[dict[str, Any], ...] = ()
    blockers: tuple[str, ...] = ()
    menu_present: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "closed_label": self.closed_label,
            "model": self.model,
            "power_index": self.power_index,
            "power_total": self.power_total,
            "power_name": self.power_name,
            "models_available": list(self.models_available),
            "blockers": list(self.blockers),
            "menu_present": self.menu_present,
        }


@dataclass
class ChatgptComSeatTarget:
    model: str | None = None
    power: str | None = None
    power_index: int | None = None
    allow_partial: bool = False


class ChatgptPage(Protocol):
    async def evaluate(self, expression: str) -> Any: ...

    async def keyboard(self) -> Any: ...

    def locator(self, selector: str) -> Any: ...

    async def wait_for_timeout(self, ms: int) -> None: ...


def parse_power_status(text: str) -> tuple[str | None, int | None, int | None]:
    """Parse ``Medium, 2 of 4.`` into name, index, total."""
    raw = (text or "").strip()
    if not raw:
        return None, None, None
    m = _POWER_STATUS_RE.match(raw)
    if not m:
        return raw, None, None
    name = m.group(1).strip()
    return name, int(m.group(2)), int(m.group(3))


def parse_seat_raw(raw: dict[str, Any] | None) -> ChatgptComSeatState:
    if not isinstance(raw, dict):
        return ChatgptComSeatState()
    models = tuple(dict(m) for m in (raw.get("models") or []) if isinstance(m, dict))
    checked = next((m for m in models if m.get("checked")), None)
    model_label = str(checked.get("label") or "").strip() if checked else None
    power_name, power_index, power_total = parse_power_status(str(raw.get("statusText") or ""))
    blockers = tuple(str(b) for b in (raw.get("blockers") or []) if b)
    return ChatgptComSeatState(
        closed_label=str(raw.get("closedLabel") or "").strip(),
        model=model_label or None,
        power_index=power_index,
        power_total=power_total,
        power_name=power_name,
        models_available=models,
        blockers=blockers,
        menu_present=bool(raw.get("menuPresent")),
    )


def normalize_seat_target(raw: dict[str, Any] | ChatgptComSeatTarget | None) -> ChatgptComSeatTarget | None:
    if raw is None:
        return None
    if isinstance(raw, ChatgptComSeatTarget):
        return raw
    if not isinstance(raw, dict):
        return None
    model = str(raw.get("model") or "").strip() or None
    power_raw = raw.get("power")
    power = str(power_raw).strip().lower() if power_raw is not None else None
    if power:
        power = _POWER_ALIASES.get(power.replace("_", " "), power)
    power_index = raw.get("power_index")
    idx = int(power_index) if power_index is not None else None
    allow_partial = bool(raw.get("allow_partial"))
    if not model and power is None and idx is None:
        return None
    return ChatgptComSeatTarget(
        model=model,
        power=power,
        power_index=idx,
        allow_partial=allow_partial,
    )


def resolve_desired_power_index(
    target: ChatgptComSeatTarget,
    state: ChatgptComSeatState,
) -> int | None:
    if target.power_index is not None:
        return target.power_index
    total = state.power_total or 4
    if not target.power:
        return None
    key = target.power.replace("_", " ").lower()
    if key in ("max", "extra high"):
        return total
    if key == "high":
        return min(3, total)
    if key == "medium":
        return min(2, total)
    if key in ("instant", "low"):
        return 1
    return None


def seat_satisfies(state: ChatgptComSeatState, target: ChatgptComSeatTarget) -> bool:
    if target.model and (state.model or "").strip() != target.model.strip():
        return False
    desired = resolve_desired_power_index(target, state)
    if desired is not None and state.power_index != desired:
        return False
    return True


def seat_target_from_pipeline_options(opts: dict[str, Any] | None) -> ChatgptComSeatTarget | None:
    if not opts:
        return None
    for key in ("chatgpt_seat", "seat", "orion_seat"):
        nested = opts.get(key)
        if nested:
            return normalize_seat_target(nested)
    return None


async def read_chatgpt_com_seat(page: ChatgptPage) -> ChatgptComSeatState:
    raw = await page.evaluate(_READ_SEAT_JS)
    return parse_seat_raw(raw if isinstance(raw, dict) else {})


async def _open_model_menu(page: ChatgptPage) -> bool:
    picker = page.locator(MODEL_PICKER_SELECTOR).first
    if not await picker.count():
        return False
    await picker.click()
    await page.wait_for_timeout(350)
    return True


async def _close_model_menu(page: ChatgptPage) -> None:
    kb = await page.keyboard()
    await kb.press("Escape")
    await page.wait_for_timeout(150)


async def _select_model_radio(page: ChatgptPage, label: str) -> bool:
    want = label.strip()
    radio = page.get_by_role("menuitemradio", name=want, exact=True)
    if not await radio.count():
        radio = page.locator('[role=menuitemradio]').filter(has_text=re.compile(rf"^{re.escape(want)}\\b"))
    if not await radio.count():
        return False
    first = radio.first
    checked = await first.get_attribute("aria-checked")
    if checked == "true":
        return True
    disabled = await first.get_attribute("aria-disabled")
    if disabled == "true":
        return False
    await first.click()
    await page.wait_for_timeout(250)
    return True


async def _adjust_power_index(page: ChatgptPage, desired: int, *, max_steps: int = 8) -> bool:
    status = page.locator('[role=menu] [role=status]').first
    if not await status.count():
        status = page.locator("[role=status]").first
    if not await status.count():
        return False
    await status.click()
    await page.wait_for_timeout(120)
    kb = await page.keyboard()
    for _ in range(max_steps):
        state = parse_seat_raw(await page.evaluate(_READ_SEAT_JS))
        current = state.power_index
        total = state.power_total or 4
        if current == desired:
            return True
        if current is None:
            return False
        if current < desired:
            await kb.press("ArrowRight")
        else:
            await kb.press("ArrowLeft")
        await page.wait_for_timeout(180)
    final = parse_seat_raw(await page.evaluate(_READ_SEAT_JS))
    return final.power_index == desired


async def ensure_chatgpt_com_seat(
    page: ChatgptPage,
    target: dict[str, Any] | ChatgptComSeatTarget,
    *,
    allow_partial: bool | None = None,
) -> dict[str, Any]:
    """Idempotent model/power ensure; returns receipt with before/after and outcome."""
    normalized = normalize_seat_target(target)
    if normalized is None:
        return {"outcome": "no_target", "ok": True}
    if allow_partial is not None:
        normalized.allow_partial = allow_partial

    if not await _open_model_menu(page):
        return {
            "outcome": "picker_missing",
            "ok": False,
            "seat_before": None,
            "seat_after": None,
            "actions": [],
        }

    before = await read_chatgpt_com_seat(page)
    actions: list[str] = []

    if seat_satisfies(before, normalized):
        await _close_model_menu(page)
        return {
            "outcome": "already_set",
            "ok": True,
            "seat_before": before.as_dict(),
            "seat_after": before.as_dict(),
            "actions": actions,
        }

    if normalized.model and (before.model or "") != normalized.model:
        if await _select_model_radio(page, normalized.model):
            actions.append(f"model:{normalized.model}")
        else:
            await _close_model_menu(page)
            after = await read_chatgpt_com_seat(page)
            return {
                "outcome": "partial",
                "ok": False,
                "seat_before": before.as_dict(),
                "seat_after": after.as_dict(),
                "actions": actions,
                "reason": "model_select_failed",
            }

    mid = await read_chatgpt_com_seat(page)
    desired_power = resolve_desired_power_index(normalized, mid)
    if desired_power is not None and mid.power_index != desired_power:
        if "usage_limit_reached" in mid.blockers and desired_power == (mid.power_total or 4):
            if not normalized.allow_partial:
                await _close_model_menu(page)
                after = await read_chatgpt_com_seat(page)
                return {
                    "outcome": "partial",
                    "ok": False,
                    "seat_before": before.as_dict(),
                    "seat_after": after.as_dict(),
                    "actions": actions,
                    "reason": "usage_limit_reached",
                    "blockers": list(mid.blockers),
                }
        moved = await _adjust_power_index(page, desired_power)
        if moved:
            actions.append(f"power_index:{desired_power}")
        elif not normalized.allow_partial:
            await _close_model_menu(page)
            after = await read_chatgpt_com_seat(page)
            return {
                "outcome": "partial",
                "ok": False,
                "seat_before": before.as_dict(),
                "seat_after": after.as_dict(),
                "actions": actions,
                "reason": "power_adjust_failed",
            }

    after = await read_chatgpt_com_seat(page)
    await _close_model_menu(page)

    satisfied = seat_satisfies(after, normalized)
    outcome = "updated" if satisfied else "partial"
    ok = satisfied or normalized.allow_partial
    receipt: dict[str, Any] = {
        "outcome": outcome,
        "ok": ok,
        "seat_before": before.as_dict(),
        "seat_after": after.as_dict(),
        "actions": actions,
    }
    if after.blockers:
        receipt["blockers"] = list(after.blockers)
    if not satisfied and not normalized.allow_partial:
        receipt["reason"] = "readback_mismatch"
    return receipt
