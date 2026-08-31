#!/usr/bin/env python3
"""Live-DOM census for cdp/opus-5 effort picker (a:31333 + a:31534).

a:31333 asked whether a trailing PUA on the Opus row caused
``effort_trigger_missing``. That glyph is real and benign.

a:31534 is the live after-ship failure: family-click lands Opus on Max, then
High fails. Stock ``opus-5-max`` PASS is **not** this AC-0. The High-after-Max
leg (``set_effort("high")`` + ``select_model("opus-5")``) is the verdict that
matters.

Opens a brand-new isolated tab (never touches an existing live CSE session).

Run on the CDP host (io, per current topology; script itself is host-agnostic
via ``BROWSER_CDP_URL``):
    ~/.venvs/universal/bin/python scripts/cortex/cdp_model_effort_census.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.chat_model_effort import (  # noqa: E402
    _effort_option,
    _effort_trigger,
    set_effort,
)
from claude_bundles.chat_model_match import label_satisfies_request  # noqa: E402
from claude_bundles.chat_model_select import (  # noqa: E402
    _click_family_radio,
    _open_picker,
    current_model_label,
    list_picker_radios,
    select_model,
)
from effort_vocabulary import to_testid as _effort_testid  # noqa: E402
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL, connect_cdp  # noqa: E402

_MENU_SELECTOR = "[role=menuitem], [role=menuitemradio]"


def _codepoints(text: str) -> list[str]:
    return [f"U+{ord(ch):04X}" for ch in text]


async def _census_menu(page) -> list[dict[str, Any]]:
    """Full role/aria/text/codepoint/outerHTML census of the open picker menu.

    This is the exact surface ``_effort_trigger`` filters against
    (``has_text=_EFFORT_ROW``) — captured raw so a missing "Effort" row and a
    PUA-suffixed label are distinguishable in the same dump.
    """
    raw = await page.evaluate(
        """(sel) => {
          const out = [];
          for (const el of document.querySelectorAll(sel)) {
            const aria = el.getAttribute('aria-label') || '';
            const text = (el.innerText || el.textContent || '').trim();
            out.push({
              role: el.getAttribute('role') || '',
              aria,
              text,
              offsetParent: !!el.offsetParent,
              outerHTML: (el.outerHTML || '').slice(0, 500),
            });
          }
          return out;
        }""",
        _MENU_SELECTOR,
    )
    rows = list(raw or [])
    for row in rows:
        row["text_codepoints"] = _codepoints(str(row.get("text") or ""))
    return rows


async def main() -> int:
    pw, _browser, ctx, _existing_page = await connect_cdp(DEFAULT_CDP_URL)
    page = await ctx.new_page()  # isolated — never touch a live CSE tab
    report: dict[str, Any] = {}
    try:
        await page.goto(
            "https://claude.ai/new", wait_until="domcontentloaded", timeout=30000
        )
        report["before"] = await current_model_label(page)

        # Step 1: open the picker, select the Opus 5 family radio, then — same
        # sequence _apply_effort actually runs (Escape + re-open) — census the
        # RE-OPENED picker. That re-open is the exact moment _effort_trigger
        # looks for an "Effort" [role=menuitem] row and can fail with
        # effort_trigger_missing; censusing right after the family click
        # alone (without the re-open) inspects the wrong moment.
        try:
            await _open_picker(page)
            report["family_radios_before_click"] = await list_picker_radios(page)
            report["family_click_matched"] = await _click_family_radio(
                page, "opus-5"
            )
            report["menu_census_immediately_after_family_click"] = (
                await _census_menu(page)
            )
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
            await _open_picker(page)
            report["menu_census_after_reopen"] = await _census_menu(page)
            trigger_loc, trigger_via = await _effort_trigger(page)
            report["effort_trigger_found"] = trigger_loc is not None
            report["effort_trigger_via"] = trigger_via
            # Picker from the reopen (above) is still open here — run the real
            # set_effort against it directly for the ground-truth verdict.
            report["set_effort_max_result"] = await set_effort(page, "max")
            report["after_family_click_label"] = await current_model_label(page)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
            await _open_picker(page)
            high_opt, high_via = await _effort_option(
                page, "high", _effort_testid("high") or "effort-option-high"
            )
            report["high_option_found_before_trigger"] = high_opt is not None
            report["high_option_via_before_trigger"] = high_via
            report["set_effort_high_result"] = await set_effort(page, "high")
        except Exception as exc:  # noqa: BLE001 — partial census beats none
            report["step1_error"] = repr(exc)

        # Max-only probe — recorded, not the a:31534 verdict.
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
        max_result = await select_model(page, "opus-5-max")
        report["select_model_opus5_max"] = max_result
        report["verdict_max"] = "PASS" if bool(max_result.get("ok")) else "FAIL"

        # a:31534 AC-0 — High after family default Max.
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
        high_result = await select_model(page, "opus-5")
        report["select_model_opus5"] = high_result
        after_high = high_result.get("current_model") or await current_model_label(
            page
        )
        high_ok = bool(high_result.get("ok")) and label_satisfies_request(
            "opus-5", after_high, effort="high"
        )
        report["after_high_label"] = after_high
        report["verdict_high_after_max"] = "PASS" if high_ok else "FAIL"
        report["verdict"] = report["verdict_high_after_max"]

        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["verdict"] == "PASS" else 1
    finally:
        await page.close()
        await pw.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
