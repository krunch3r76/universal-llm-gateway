"""Browser action primitives for Playwright-backed interactive workflows.

Two kinds of primitives:

- ``apply_wait_for(page, spec)`` — applied once before an action sequence runs,
  to gate on page readiness (selector appearance, networkidle, fixed delay).
- ``execute_action(page, action)`` — single-step interaction (click, fill,
  press, select_option, hover) or in-sequence wait. Dispatched by ``type``.

Invariants:
- ∀ spec/action: ``type`` field present and valid; unknown types raise
  ``ActionError`` so the caller can surface the failing index.
- ∀ selector-bearing actions: selector required; no default targeting.
- ∀ timeouts: default 30_000ms unless the action spec overrides.
"""

from __future__ import annotations

from typing import Any

DEFAULT_TIMEOUT_MS = 30_000
DEFAULT_WAIT_STATE = "visible"

SUPPORTED_WAIT_TYPES = frozenset({"selector", "networkidle", "timeout_ms"})
SUPPORTED_ACTION_TYPES = frozenset(
    {
        "click",
        "fill",
        "press",
        "select_option",
        "hover",
        "wait_for_selector",
        "wait_for_timeout",
        "wait_for_networkidle",
    }
)


class ActionError(ValueError):
    """Raised when a wait_for spec or action cannot be executed.

    Callers should catch and surface ``idx`` (the zero-based position of the
    failing action in the sequence, or ``-1`` for a top-level wait_for).
    """


async def apply_wait_for(page: Any, spec: dict[str, Any]) -> None:
    """Gate page readiness before the action sequence runs.

    Supported ``type`` values:
      * ``selector`` — wait for ``value`` selector in ``state`` (default "visible")
      * ``networkidle`` — wait for no network activity for 500ms
      * ``timeout_ms`` — fixed sleep, ``value`` ms
    """
    kind = spec.get("type")
    if kind not in SUPPORTED_WAIT_TYPES:
        raise ActionError(
            f"Unknown wait_for type: {kind!r} (supported: {sorted(SUPPORTED_WAIT_TYPES)})"
        )

    timeout_ms = int(spec.get("timeout_ms", DEFAULT_TIMEOUT_MS))

    if kind == "selector":
        selector = spec.get("value")
        if not selector:
            raise ActionError("wait_for type=selector requires 'value' (CSS selector)")
        state = spec.get("state", DEFAULT_WAIT_STATE)
        await page.wait_for_selector(selector, timeout=timeout_ms, state=state)
        return

    if kind == "networkidle":
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
        return

    # timeout_ms
    duration = int(spec.get("value", timeout_ms))
    await page.wait_for_timeout(duration)


async def execute_action(page: Any, action: dict[str, Any]) -> None:
    """Execute a single browser action. Raises ``ActionError`` on unknown type.

    Action types:
      * ``click`` — selector; optional button, click_count, timeout_ms
      * ``fill`` — selector, value; optional timeout_ms
      * ``press`` — key; optional selector (targets that element), timeout_ms
      * ``select_option`` — selector, one of value/label/index; optional timeout_ms
      * ``hover`` — selector; optional timeout_ms
      * ``wait_for_selector`` — selector; optional state, timeout_ms
      * ``wait_for_timeout`` — timeout_ms (required)
      * ``wait_for_networkidle`` — optional timeout_ms
    """
    kind = action.get("type")
    if kind not in SUPPORTED_ACTION_TYPES:
        raise ActionError(
            f"Unknown action type: {kind!r} (supported: {sorted(SUPPORTED_ACTION_TYPES)})"
        )

    timeout_ms = int(action.get("timeout_ms", DEFAULT_TIMEOUT_MS))

    if kind == "click":
        selector = _require_selector(action)
        await page.click(
            selector,
            timeout=timeout_ms,
            button=action.get("button", "left"),
            click_count=int(action.get("click_count", 1)),
        )
        return

    if kind == "fill":
        selector = _require_selector(action)
        value = action.get("value")
        if value is None:
            raise ActionError("action type=fill requires 'value'")
        await page.fill(selector, str(value), timeout=timeout_ms)
        return

    if kind == "press":
        key = action.get("key")
        if not key:
            raise ActionError("action type=press requires 'key'")
        selector = action.get("selector")
        if selector:
            await page.press(selector, key, timeout=timeout_ms)
        else:
            await page.keyboard.press(key)
        return

    if kind == "select_option":
        selector = _require_selector(action)
        option_kwargs: dict[str, Any] = {}
        for key_name in ("value", "label", "index"):
            if key_name in action:
                option_kwargs[key_name] = action[key_name]
        if not option_kwargs:
            raise ActionError(
                "action type=select_option requires one of value/label/index"
            )
        await page.select_option(selector, timeout=timeout_ms, **option_kwargs)
        return

    if kind == "hover":
        selector = _require_selector(action)
        await page.hover(selector, timeout=timeout_ms)
        return

    if kind == "wait_for_selector":
        selector = _require_selector(action)
        state = action.get("state", DEFAULT_WAIT_STATE)
        await page.wait_for_selector(selector, timeout=timeout_ms, state=state)
        return

    if kind == "wait_for_timeout":
        duration = action.get("timeout_ms")
        if duration is None:
            raise ActionError("action type=wait_for_timeout requires 'timeout_ms'")
        await page.wait_for_timeout(int(duration))
        return

    # wait_for_networkidle
    await page.wait_for_load_state("networkidle", timeout=timeout_ms)


def _require_selector(action: dict[str, Any]) -> str:
    """Extract the required ``selector`` field or raise."""
    selector = action.get("selector")
    if not selector or not isinstance(selector, str):
        raise ActionError(
            f"action type={action.get('type')!r} requires 'selector' (CSS selector)"
        )
    return selector
