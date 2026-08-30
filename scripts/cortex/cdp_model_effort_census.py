#!/usr/bin/env python3
"""Live-DOM census for the cdp/opus-5 effort-picker break (a:31333).

Friction a:31333 theorizes a live claude.ai UI change: the "Opus 5" model-list
row now carries a trailing Private-Use-Area glyph (U+E03B) that Fable/Sonnet/
Haiku rows don't have, and that this is why ``set_effort`` fails at
``effort_trigger_missing``. Reading the code shows that glyph only appears in
the *diagnostic* ``available_models`` dump from the qualified-radio recovery
fallback (``_recover_effort_via_qualified_radio``) — a different DOM surface
than the ``[role=menuitem]`` "Effort" row ``_effort_trigger`` actually looks
for. This script censuses both surfaces on a live page before any fix is
written, so the diff targets the confirmed cause rather than the friction's
own (plausible but unverified) theory — same discipline that let a:31319 land
on its real cause (aria-label dropped) instead of its own PUA-prefix theory.

Opens a brand-new isolated tab (never touches an existing live CSE session),
lands on ``https://claude.ai/new``, opens the model picker, selects the
Opus 5 family radio, and censuses the resulting menu (role/aria/text/raw
codepoints/outerHTML) — the exact state ``_effort_trigger`` inspects — before
running the real ``select_model`` end-to-end for the official verdict.

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

from claude_bundles.chat_model_effort import _effort_trigger, set_effort  # noqa: E402
from claude_bundles.chat_model_select import (  # noqa: E402
    _click_family_radio,
    _open_picker,
    current_model_label,
    list_picker_radios,
    select_model,
)
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
        except Exception as exc:  # noqa: BLE001 — partial census beats none
            report["step1_error"] = repr(exc)

        # Step 2: run the real production call end-to-end for the official
        # verdict — request an effort ("max") distinct from whatever "before"
        # showed, so the already_selected/already_on_label fast paths can't
        # mask a genuine submenu failure.
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
        result = await select_model(page, "opus-5-max")
        report["select_model_result"] = result
        report["verdict"] = "PASS" if bool(result.get("ok")) else "FAIL"

        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["verdict"] == "PASS" else 1
    finally:
        await page.close()
        await pw.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
