"""Compose Chat/Cowork chip click + gate-reject / radiogroup probe (arc 6928).

The click path is shadow-piercing role/aria; the light-DOM census is narrower.
Size/visibility/offsetParent rejects used to be silent — this module records
them so a residual ``chip_missing`` can answer the Fork A falsifier (chip
rendered but gate-rejected) without another dispatch.
"""

from __future__ import annotations

import re
from typing import Any


async def chip_center(page, label: str) -> dict[str, float] | None:
    """Return clickable center for ``label`` chip, or None if size-gated out."""
    return await page.evaluate(
        """(label) => {
          for (const el of document.querySelectorAll('span,button,div,[role=button],[role=radio]')) {
            if ((el.innerText || '').trim() !== label) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 20 || r.width > 200 || r.height < 16 || r.height > 48) {
              continue;
            }
            return {x: r.x + r.width / 2, y: r.y + r.height / 2};
          }
          return null;
        }""",
        label,
    )


async def scroll_click(loc) -> None:
    """Bring chip into viewport then force-click (segmented controls clip)."""
    try:
        await loc.scroll_into_view_if_needed(timeout=3000)
    except Exception:  # noqa: BLE001 — best-effort; force click still runs
        pass
    await loc.click(force=True)


async def collect_chip_candidates(page, label: str) -> list[dict[str, Any]]:
    """Census Cowork/Chat nodes including offsetParent/size rejects for dumps."""
    raw = await page.evaluate(
        """(label) => {
          const out = [];
          for (const el of document.querySelectorAll('span,button,div,[role=button],[role=radio]')) {
            const text = (el.innerText || '').trim();
            if (text !== label) continue;
            const r = el.getBoundingClientRect();
            const offsetOk = !!el.offsetParent;
            const sizeOk = !(r.width < 20 || r.width > 220 || r.height < 14);
            let reject = null;
            if (!offsetOk) reject = 'offsetParent';
            else if (!sizeOk) reject = 'size';
            out.push({
              text,
              tag: el.tagName,
              role: el.getAttribute('role') || '',
              w: r.width,
              h: r.height,
              offsetParent: offsetOk,
              reject,
            });
          }
          return out;
        }""",
        label,
    )
    return list(raw or [])


async def collect_radiogroup_evidence(page) -> dict[str, Any]:
    """Surface radiogroup count + accessible names for joinable failure dumps."""
    raw = await page.evaluate(
        """() => {
          const groups = [...document.querySelectorAll('[role=radiogroup]')];
          const names = groups.map((g) => {
            const labelled = g.getAttribute('aria-label')
              || (g.getAttribute('aria-labelledby')
                    ? (document.getElementById(g.getAttribute('aria-labelledby')) || {}).innerText
                    : '')
              || '';
            return (labelled || '').trim().slice(0, 80);
          }).filter(Boolean);
          const surface = groups.filter((g) => {
            const n = (g.getAttribute('aria-label') || '').toLowerCase();
            return n === 'surface';
          }).length;
          return {radiogroup_names: names, surface_radiogroup_count: surface, radiogroup_count: groups.length};
        }"""
    )
    if not isinstance(raw, dict):
        return {
            "radiogroup_names": [],
            "surface_radiogroup_count": 0,
            "radiogroup_count": 0,
        }
    return {
        "radiogroup_names": list(raw.get("radiogroup_names") or []),
        "surface_radiogroup_count": int(raw.get("surface_radiogroup_count") or 0),
        "radiogroup_count": int(raw.get("radiogroup_count") or 0),
    }


async def collect_approval_candidates(page) -> list[dict[str, Any]]:
    """Census Auto/Manual/Skip approval-control candidates for failure dumps.

    a:31319 — the approval chip can render aria-label-less with a bare
    "Auto"/"Manual"/"Skip" text label, so this matches on *either* aria or
    text against the full approval vocabulary rather than one exact label
    (contrast ``collect_chip_candidates``, which needs an exact Chat/Cowork
    match). Verified shape via live census: a plain ``<button>``, no aria.
    """
    raw = await page.evaluate(
        """() => {
          const wanted = /auto|approve|manual|skip/i;
          const out = [];
          const sel = 'button, [role="button"], [role="radio"], [role="menuitemradio"]';
          for (const el of document.querySelectorAll(sel)) {
            const aria = el.getAttribute('aria-label') || '';
            const text = (el.innerText || '').trim().replace(/\\s+/g, ' ');
            if (!wanted.test(aria) && !wanted.test(text)) continue;
            const r = el.getBoundingClientRect();
            out.push({
              tag: el.tagName,
              role: el.getAttribute('role') || '',
              aria,
              text: text.slice(0, 60),
              offsetParent: !!el.offsetParent,
              w: r.width,
              h: r.height,
            });
          }
          return out;
        }"""
    )
    return list(raw or [])


async def collect_effort_candidates(page) -> list[dict[str, Any]]:
    """Census Effort-trigger/option candidates for failure dumps (a:31333).

    Mirrors ``collect_approval_candidates`` for the effort flyout: matches on
    either aria or text against the effort vocabulary (Effort/Low/Medium/
    High/Extra/Max) rather than requiring one exact label. Also records raw
    codepoints so a trailing icon-font glyph (confirmed benign via live
    census against production — a real ``U+E03B``/``U+E02A`` suffix that the
    existing substring-based matching already tolerates) is distinguishable
    from a genuinely missing row, rather than re-litigating that theory on
    every future ``effort_trigger_missing``.
    """
    raw = await page.evaluate(
        """() => {
          const wanted = /effort|low|medium|high|extra|max/i;
          const out = [];
          const sel = '[role="menuitem"], [role="menuitemradio"]';
          for (const el of document.querySelectorAll(sel)) {
            const aria = el.getAttribute('aria-label') || '';
            const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
            if (!wanted.test(aria) && !wanted.test(text)) continue;
            const r = el.getBoundingClientRect();
            out.push({
              tag: el.tagName,
              role: el.getAttribute('role') || '',
              aria,
              text: text.slice(0, 60),
              codepoints: Array.from(text).map((ch) => 'U+' + ch.codePointAt(0).toString(16).toUpperCase().padStart(4, '0')),
              offsetParent: !!el.offsetParent,
              w: r.width,
              h: r.height,
            });
          }
          return out;
        }"""
    )
    return list(raw or [])


def _size_reject(box: dict[str, float] | None) -> dict[str, Any] | None:
    if not box:
        return {"reason": "no_box"}
    w, h = box.get("width", 0), box.get("height", 0)
    if w < 20 or w > 220 or h < 14:
        return {"reason": "size", "w": w, "h": h}
    return None


async def try_click_compose_chip(page, label: str) -> tuple[str | None, dict[str, Any]]:
    """Click Chat/Cowork chip if visible; record silent gate rejects.

    Prefer Surface radiogroup; fallback unscoped role/text then mouse center.
    Returns ``(via_or_None, probe)`` where ``probe`` always carries radiogroup
    evidence and any ``gate_rejects`` accumulated on this attempt.
    """
    # Sidebar owns aria-label="Mode" (Home/Code) — compose toggle is Surface + radio.
    rg = await collect_radiogroup_evidence(page)
    rejects: list[dict[str, Any]] = []
    scoped = page.get_by_role("radiogroup", name="Surface").get_by_role(
        "radio", name=re.compile(rf"^{re.escape(label)}$", re.I)
    )
    surface_radio_count = await scoped.count()
    if surface_radio_count:
        btn = scoped.first
        if not await btn.is_visible():
            rejects.append(
                {
                    "arm": "surface",
                    "label": label,
                    "reason": "not_visible",
                    "count": surface_radio_count,
                }
            )
        else:
            box = await btn.bounding_box()
            size_rej = _size_reject(box)
            if size_rej:
                rejects.append({"arm": "surface", "label": label, **size_rej})
            else:
                await scroll_click(btn)
                return "playwright_surface", {
                    **rg,
                    "via": "playwright_surface",
                    "gate_rejects": rejects,
                    "surface_radio_count": surface_radio_count,
                }
    else:
        # Surface group missing or radio absent — still useful for falsifier.
        if rg["surface_radiogroup_count"] == 0 and rg["radiogroup_count"] == 0:
            rejects.append(
                {"arm": "surface", "label": label, "reason": "radiogroup_absent"}
            )
        else:
            rejects.append(
                {
                    "arm": "surface",
                    "label": label,
                    "reason": "radio_absent",
                    "surface_radiogroup_count": rg["surface_radiogroup_count"],
                }
            )

    for name in (label, label.lower(), label.upper()):
        for role in ("tab", "radio", "button"):
            loc = page.get_by_role(
                role, name=re.compile(rf"^{re.escape(name)}$", re.I)
            )
            if not await loc.count():
                continue
            btn = loc.first
            if not await btn.is_visible():
                rejects.append(
                    {"arm": f"unscoped_{role}", "label": name, "reason": "not_visible"}
                )
                continue
            box = await btn.bounding_box()
            size_rej = _size_reject(box)
            if size_rej:
                rejects.append({"arm": f"unscoped_{role}", "label": name, **size_rej})
                continue
            await scroll_click(btn)
            return "playwright", {
                **rg,
                "via": "playwright",
                "gate_rejects": rejects,
                "surface_radio_count": surface_radio_count,
            }
        text_loc = page.get_by_text(name, exact=True)
        if not await text_loc.count():
            continue
        btn = text_loc.first
        if not await btn.is_visible():
            rejects.append(
                {"arm": "unscoped_text", "label": name, "reason": "not_visible"}
            )
            continue
        box = await btn.bounding_box()
        size_rej = _size_reject(box)
        if size_rej:
            rejects.append({"arm": "unscoped_text", "label": name, **size_rej})
            continue
        await scroll_click(btn)
        return "playwright", {
            **rg,
            "via": "playwright",
            "gate_rejects": rejects,
            "surface_radio_count": surface_radio_count,
        }

    box = await chip_center(page, label)
    if box:
        await page.mouse.click(box["x"], box["y"])
        return "mouse", {
            **rg,
            "via": "mouse",
            "gate_rejects": rejects,
            "surface_radio_count": surface_radio_count,
        }
    rejects.append({"arm": "mouse_center", "label": label, "reason": "no_center"})
    return None, {
        **rg,
        "via": None,
        "gate_rejects": rejects,
        "surface_radio_count": surface_radio_count,
    }
