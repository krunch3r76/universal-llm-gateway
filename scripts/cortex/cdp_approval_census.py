#!/usr/bin/env python3
"""Live-fire verification of the ``ensure_cowork_auto`` selector fix (a:31319).

Opens a brand-new isolated tab (never touches an existing live CSE session),
lands on ``https://claude.ai/new``, and calls the real ``ensure_cowork_auto``
end-to-end — the same call every CDP dispatch makes before Start task. Prints
the full result so the fix can be confirmed against production, not just the
hermetic test fixtures.

``--trace`` does not call ``set_approval_mode`` or Start task / Send. It
records the aria-less approval chip, clicks it once via DOM ``el.click()`` and
once via Playwright (no force), and prints the portal/row diff. Default
(no ``--trace``) behaviour is unchanged.

Run on Jupiter (CDP host):
    ~/.venvs/universal/bin/python scripts/cortex/cdp_approval_census.py
    ~/.venvs/universal/bin/python scripts/cortex/cdp_approval_census.py --trace
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.chat_cowork_mode import (  # noqa: E402
    ensure_cowork_auto,
    select_compose_mode,
)
from claude_bundles.compose_attest import (  # noqa: E402
    compose_mode_fingerprint,
    cowork_auto_refuse_reason,
)
from claude_bundles.compose_chip_probe import collect_approval_candidates  # noqa: E402
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL, connect_cdp  # noqa: E402

# Chip find matches ``_click_approval_chip_js``. Stamp is applied after
# outerHTML is recorded so the dump is the live chip, not our marker.
_CAPTURE_JS = """() => {
  const isVisible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    if (Number(s.opacity) === 0) return false;
    return true;
  };
  const boxOf = (el) => {
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.x),
      y: Math.round(r.y),
      w: Math.round(r.width * 100) / 100,
      h: Math.round(r.height * 100) / 100,
    };
  };
  const describe = (el, via) => ({
    via,
    tag: el.tagName,
    role: el.getAttribute('role') || '',
    aria: (el.getAttribute('aria-label') || '').slice(0, 80),
    text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
    dataState: el.getAttribute('data-state') || '',
    id: el.id || '',
    className: String(el.className || '').slice(0, 120),
    box: boxOf(el),
  });
  const re = /^(auto|manual|skip)$/i;
  let best = null;
  for (const el of document.querySelectorAll('button, [role="button"]')) {
    const text = (el.innerText || '').trim();
    if (!re.test(text)) continue;
    if (!el.offsetParent) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 28 || r.width > 140 || r.height < 16 || r.height > 48) continue;
    if (!best || r.width < best.w) best = {el, w: r.width};
  }
  let chip = {found: false};
  if (best) {
    const el = best.el;
    chip = {
      found: true,
      outerHTML: (el.outerHTML || '').slice(0, 600),
      attrs: {
        'aria-expanded': el.getAttribute('aria-expanded'),
        'aria-haspopup': el.getAttribute('aria-haspopup'),
        'data-state': el.getAttribute('data-state'),
        'aria-controls': el.getAttribute('aria-controls'),
        id: el.id || '',
        role: el.getAttribute('role') || '',
        'aria-label': el.getAttribute('aria-label'),
      },
      text: (el.innerText || '').trim(),
      tag: el.tagName,
      box: boxOf(el),
    };
    el.setAttribute('data-ulg-trace-chip', '1');
  }
  const groups = [...document.querySelectorAll('[role=radiogroup]')];
  const names = groups.map((g) => (g.getAttribute('aria-label') || '').trim()).filter(Boolean);
  const mode = groups.find((g) => /^mode$/i.test(g.getAttribute('aria-label') || ''));
  const childDesc = (el) => ({
    tag: el.tagName,
    role: el.getAttribute('role') || '',
    aria: el.getAttribute('aria-label') || '',
    text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
    ariaChecked: el.getAttribute('aria-checked'),
  });
  const modeGroup = mode
    ? {
        found: true,
        children: [...mode.children].slice(0, 24).map(childDesc),
        radios: [...mode.querySelectorAll('[role=radio]')].slice(0, 24).map(childDesc),
      }
    : {found: false, radiogroup_names: names};
  const portals = [];
  const seen = new Set();
  const push = (el, via) => {
    if (!el || seen.has(el) || !isVisible(el)) return;
    seen.add(el);
    portals.push(describe(el, via));
  };
  for (const el of document.body ? document.body.children : []) push(el, 'body-child');
  for (const el of document.querySelectorAll(
    '[data-radix-popper-content-wrapper], [role=menu], [role=listbox], [role=dialog]'
  )) {
    push(el, 'portal-sel');
  }
  return {chip, mode_radiogroup: modeGroup, portals};
}"""

_JS_CLICK = """() => {
  const el = document.querySelector('[data-ulg-trace-chip="1"]');
  if (!el) return {ok: false, reason: 'stamp_missing'};
  el.click();
  return {ok: true};
}"""

_ROW_JS = """() => {
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    return true;
  };
  const boxOf = (el) => {
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.x),
      y: Math.round(r.y),
      w: Math.round(r.width * 100) / 100,
      h: Math.round(r.height * 100) / 100,
    };
  };
  const roots = [];
  const seenRoot = new Set();
  const addRoot = (el) => {
    if (!el || seenRoot.has(el) || !isVisible(el)) return;
    seenRoot.add(el);
    roots.push(el);
  };
  for (const el of document.querySelectorAll(
    '[data-radix-popper-content-wrapper], [role=menu], [role=listbox], [role=dialog]'
  )) addRoot(el);
  const sel = [
    '[role="menuitemradio"]',
    '[role="menuitem"]',
    '[role="option"]',
    '[role="radio"]',
    '[role="menuitemcheckbox"]',
    'button',
    '[role="button"]',
    'a',
    'li',
  ].join(', ');
  const rows = [];
  const seen = new Set();
  for (const root of roots) {
    const nodes = [];
    if (root.matches(sel)) nodes.push(root);
    nodes.push(...root.querySelectorAll(sel));
    for (const el of nodes) {
      if (seen.has(el) || !isVisible(el)) continue;
      const text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
      const aria = el.getAttribute('aria-label') || '';
      if (!text && !aria) continue;
      seen.add(el);
      rows.push({
        tag: el.tagName,
        role: el.getAttribute('role') || '',
        aria: aria.slice(0, 80),
        text: text.slice(0, 80),
        dataState: el.getAttribute('data-state') || '',
        box: boxOf(el),
      });
      if (rows.length >= 40) return rows;
    }
  }
  return rows;
}"""


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trace",
        action="store_true",
        help=(
            "After select_compose_mode(cowork), trace the approval chip "
            "click. Does not call set_approval_mode or Start task/Send."
        ),
    )
    return parser.parse_args(argv)


def _portal_sig(node: dict[str, Any]) -> str:
    return "|".join(
        str(node.get(key) or "")
        for key in ("via", "tag", "role", "id", "aria", "text", "dataState")
    )


def _new_portals(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    old = {_portal_sig(node) for node in before}
    return [node for node in after if _portal_sig(node) not in old]


async def _capture(page) -> dict[str, Any]:
    raw = await page.evaluate(_CAPTURE_JS)
    if not isinstance(raw, dict):
        raw = {
            "chip": {"found": False},
            "mode_radiogroup": {"found": False},
            "portals": [],
        }
    raw["fingerprint"] = await compose_mode_fingerprint(page)
    return raw


async def _run_trace(page) -> dict[str, Any]:
    """Discriminate H1–H5. Never calls set_approval_mode or clicks a menu row."""
    select = await select_compose_mode(page, "cowork")
    before = await _capture(page)
    before["candidates"] = await collect_approval_candidates(page)
    js_click = await page.evaluate(_JS_CLICK)
    await page.wait_for_timeout(800)
    after_js = await _capture(page)
    after_js["rows"] = await page.evaluate(_ROW_JS)
    after_js["portals_new"] = _new_portals(
        list(before.get("portals") or []),
        list(after_js.get("portals") or []),
    )
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)
    pw_click: dict[str, Any]
    loc = page.locator("[data-ulg-trace-chip='1']")
    try:
        if await loc.count() == 0:
            pw_click = {"ok": False, "reason": "stamp_missing"}
        else:
            await loc.first.click(timeout=8000)
            pw_click = {"ok": True, "force": False}
    except Exception as exc:
        pw_click = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    await page.wait_for_timeout(800)
    after_pw = await _capture(page)
    after_pw["rows"] = await page.evaluate(_ROW_JS)
    after_pw["portals_new"] = _new_portals(
        list(before.get("portals") or []),
        list(after_pw.get("portals") or []),
    )
    await page.keyboard.press("Escape")
    return {
        "mode": "trace",
        "url": page.url,
        "select_compose_mode": select,
        "js_click": js_click,
        "playwright_click": pw_click,
        "before": before,
        "after_js_click": after_js,
        "after_playwright_click": after_pw,
    }


async def _run_census(page) -> dict[str, Any]:
    result = await ensure_cowork_auto(page)
    fp = await compose_mode_fingerprint(page)
    refuse = cowork_auto_refuse_reason(fp)
    return {
        "url": page.url,
        "ensure_cowork_auto_result": result,
        "final_fingerprint": fp,
        "cowork_auto_refuse_reason": refuse,
        "verdict": "PASS" if bool(result.get("ok")) and refuse is None else "FAIL",
    }


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        pw, _browser, ctx, _existing_page = await connect_cdp(DEFAULT_CDP_URL)
    except Exception as exc:
        if args.trace:
            print(
                json.dumps(
                    {
                        "verdict": "UNVERIFIED",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    indent=2,
                )
            )
            return 2
        raise
    page = await ctx.new_page()  # isolated — never touch a live CSE tab
    try:
        await page.goto(
            "https://claude.ai/new", wait_until="domcontentloaded", timeout=30000
        )
        if args.trace:
            report = await _run_trace(page)
            print(json.dumps(report, indent=2, default=str))
            return 0
        report = await _run_census(page)
        print(json.dumps(report, indent=2, default=str))
        return 0 if report["verdict"] == "PASS" else 1
    finally:
        await page.close()
        await pw.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
