"""claude.ai compose-mode helpers — Chat/Cowork toggle + approval.

Verified on Jupiter CDP ask profile (:9223) 2026-07-16:

- Chat ↔ Cowork chips flip document title ``New chat`` ↔ ``New task``.
- Cowork exposes approval control (aria ``Manually approve`` /
  ``Automatically approve`` / skip).
- Menu radios: Manually approve | Automatically approve | Skip all approvals.
- Desired latency-sensitive / connector consult default: Cowork + Auto.
"""

from __future__ import annotations

import re
from typing import Any, Literal

ApprovalMode = Literal["auto", "manual", "skip"]
ComposeMode = Literal["chat", "cowork"]

_APPROVAL_ARIA = {
    "auto": re.compile(r"Automatically approve", re.I),
    "manual": re.compile(r"Manually approve", re.I),
    "skip": re.compile(r"Skip all approvals|Never pause", re.I),
}

_APPROVAL_MENU = {
    "auto": re.compile(r"Automatically approve|^Auto\b", re.I),
    "manual": re.compile(r"Manually approve|^Manual\b", re.I),
    "skip": re.compile(r"Skip all approvals", re.I),
}

_APPROVAL_RADIO_TOKEN = {
    "auto": "Automatically approve",
    "manual": "Manually approve",
    "skip": "Skip all approvals",
}

_APPROVAL_RADIO_ALL = tuple(_APPROVAL_RADIO_TOKEN.values())


def exclusive_radio_text_match(text: str, token: str) -> bool:
    """True iff ``text`` names ``token`` and no sibling approval label.

    Parent menu groups concatenate Manual+Auto copy; role=name matching those
    groups mis-clicks (friction 24610). Exclusive radios pass; groups fail.
    """
    if not re.search(re.escape(token), text, re.I):
        return False
    others = [o for o in _APPROVAL_RADIO_ALL if o.lower() != token.lower()]
    return not any(re.search(re.escape(o), text, re.I) for o in others)


async def _chip_center(page, label: str) -> dict[str, float] | None:
    return await page.evaluate(
        """(label) => {
          for (const el of document.querySelectorAll('span,button,div')) {
            if ((el.innerText || '').trim() !== label) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 20 || r.width > 120 || r.height < 16 || r.height > 40) {
              continue;
            }
            return {x: r.x + r.width / 2, y: r.y + r.height / 2};
          }
          return null;
        }""",
        label,
    )


async def compose_mode_fingerprint(page) -> dict[str, Any]:
    """Lightweight attest: title + approval aria currently shown."""
    title = await page.title()
    approval = await page.evaluate(
        """() => {
          const btns = Array.from(document.querySelectorAll('button'));
          for (const b of btns) {
            const aria = b.getAttribute('aria-label') || '';
            if (/approve/i.test(aria)) {
              return {
                aria,
                text: (b.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40),
              };
            }
          }
          return null;
        }"""
    )
    mode: str | None = None
    if re.search(r"new task", title, re.I):
        mode = "cowork"
    elif re.search(r"new chat", title, re.I):
        mode = "chat"
    return {"title": title, "mode": mode, "approval": approval, "url": page.url}


async def select_compose_mode(page, mode: ComposeMode) -> dict[str, Any]:
    """Toggle Chat/Cowork segmented control on ``/new`` (pre-submit)."""
    label = "Cowork" if mode == "cowork" else "Chat"
    before = await compose_mode_fingerprint(page)
    if before.get("mode") == mode:
        return {"ok": True, "step": f"already_{mode}", "before": before, "after": before}

    box = await _chip_center(page, label)
    if not box:
        return {
            "ok": False,
            "step": "chip_missing",
            "wanted": mode,
            "before": before,
        }
    await page.mouse.click(box["x"], box["y"])
    await page.wait_for_timeout(1800)
    after = await compose_mode_fingerprint(page)
    ok = after.get("mode") == mode or (
        mode == "cowork" and bool(after.get("approval"))
    )
    return {
        "ok": ok,
        "step": f"selected_{mode}" if ok else f"select_{mode}_no_attest",
        "before": before,
        "after": after,
        "clicked": box,
    }


async def _open_approval_menu(page) -> dict[str, Any]:
    """Click current approval chip (Manual/Auto/Skip)."""
    for aria_re in (
        _APPROVAL_ARIA["auto"],
        _APPROVAL_ARIA["manual"],
        _APPROVAL_ARIA["skip"],
    ):
        loc = page.get_by_label(aria_re)
        if await loc.count():
            await loc.first.click(force=True)
            await page.wait_for_timeout(1000)
            return {"ok": True, "opened_via": "aria", "pattern": aria_re.pattern}
    # Fallback: visible Manual/Auto button text
    for pat in (r"^Manual", r"^Auto", r"^Skip"):
        loc = page.locator("button").filter(has_text=re.compile(pat, re.I))
        if await loc.count():
            await loc.first.click(force=True)
            await page.wait_for_timeout(1000)
            return {"ok": True, "opened_via": "text", "pattern": pat}
    return {"ok": False, "step": "approval_control_missing"}


async def set_approval_mode(page, mode: ApprovalMode = "auto") -> dict[str, Any]:
    """Set Cowork approval mode. Requires Cowork compose chrome."""
    before = await compose_mode_fingerprint(page)
    wanted_aria = _APPROVAL_ARIA[mode]
    if before.get("approval") and wanted_aria.search(
        before["approval"].get("aria") or ""
    ):
        return {
            "ok": True,
            "step": f"already_{mode}",
            "before": before,
            "after": before,
        }

    opened = await _open_approval_menu(page)
    if not opened.get("ok"):
        return {**opened, "before": before}

    # Prefer the dedicated menuitemradio. Parent groups concatenate Manual+Auto
    # copy, and radios lead with icon glyphs — so name=/Automatically approve/
    # mis-clicks the group (2026-07-16 :9224).
    radio_token = _APPROVAL_RADIO_TOKEN[mode]
    exclusive = await page.evaluate(
        """(token) => {
          const radios = [...document.querySelectorAll('[role=menuitemradio]')];
          const all = ['Manually approve', 'Automatically approve', 'Skip all approvals'];
          const others = all.filter((x) => x.toLowerCase() !== token.toLowerCase());
          for (const el of radios) {
            const t = (el.innerText || '').replace(/\\s+/g, ' ');
            if (!exclusiveRadioTextMatch(t, token, others)) continue;
            el.click();
            return {ok: true, text: t.slice(0, 120)};
          }
          return {ok: false, count: radios.length};
          function exclusiveRadioTextMatch(t, token, others) {
            if (!new RegExp(token, 'i').test(t)) return false;
            if (others.some((o) => new RegExp(o, 'i').test(t))) return false;
            return true;
          }
        }""",
        radio_token,
    )
    if not exclusive.get("ok"):
        menu_re = _APPROVAL_MENU[mode]
        item = page.get_by_role("menuitemradio", name=menu_re)
        if await item.count() == 0:
            item = page.get_by_role("menuitem", name=menu_re)
        if await item.count() == 0:
            return {
                "ok": False,
                "step": "menu_item_missing",
                "wanted": mode,
                "opened": opened,
                "before": before,
                "exclusive": exclusive,
            }
        await item.first.click(force=True)
    await page.wait_for_timeout(1200)
    after = await compose_mode_fingerprint(page)
    ok = bool(
        after.get("approval")
        and wanted_aria.search(after["approval"].get("aria") or "")
    )
    return {
        "ok": ok,
        "step": f"selected_{mode}" if ok else f"select_{mode}_no_attest",
        "opened": opened,
        "before": before,
        "after": after,
    }


async def ensure_cowork_auto(page) -> dict[str, Any]:
    """Select Cowork mode and Automatically approve (>> Auto).

    Call after landing on ``https://claude.ai/new``, before model pick / send.
    No-op-ish on Project shells that lack the Chat/Cowork chips (returns
    ``chip_missing`` — callers may continue without failing hard).
    """
    mode = await select_compose_mode(page, "cowork")
    if not mode.get("ok"):
        return {"ok": False, "step": "cowork", "mode": mode}
    approval = await set_approval_mode(page, "auto")
    return {
        "ok": bool(approval.get("ok")),
        "step": "cowork_auto",
        "mode": mode,
        "approval": approval,
    }
