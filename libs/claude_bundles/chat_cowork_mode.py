"""claude.ai compose-mode helpers — Chat/Cowork toggle + approval.

Verified on Jupiter CDP ask profile (:9223) 2026-07-16:

- Chat ↔ Cowork chips flip document title ``New chat`` ↔ ``New task``.
- Cowork exposes approval control (aria ``Manually approve`` /
  ``Automatically approve`` / skip).
- Menu radios: Manually approve | Automatically approve | Skip all approvals.
- Cowork + Auto default on bare ``/new`` (friction 25051).
- Chat via ``ensure_chat_compose`` — **operator-gated only** until dogfood passes.
- Toggle repair + poll-until-attest (friction 25052 — dual-primary Q1 bind).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from claude_bundles.compose_attest import (
    _POLL_MS,
    await_compose_attest,
    compose_mode_fingerprint,
)

_CHIP_POLL_TIMEOUT_S = 8.0

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


async def _scroll_click(loc) -> None:
    """Scroll segmented-control target into view before click."""
    try:
        await loc.scroll_into_view_if_needed(timeout=3000)
    except Exception:  # noqa: BLE001 — best-effort; force click still runs
        pass
    await loc.click(force=True)


async def _collect_chip_candidates(page, label: str) -> list[dict[str, Any]]:
    """Visible chip census for toggle-failure diagnostics."""
    raw = await page.evaluate(
        """(label) => {
          const out = [];
          for (const el of document.querySelectorAll('span,button,div,[role=button],[role=radio]')) {
            const text = (el.innerText || '').trim();
            if (text !== label) continue;
            if (!el.offsetParent) continue;
            const r = el.getBoundingClientRect();
            out.push({
              text,
              tag: el.tagName,
              role: el.getAttribute('role') || '',
              w: r.width,
              h: r.height,
            });
          }
          return out;
        }""",
        label,
    )
    return list(raw or [])


async def _chip_missing_payload(
    page,
    *,
    mode: ComposeMode,
    before: dict[str, Any],
) -> dict[str, Any]:
    fp = await compose_mode_fingerprint(page)
    candidates = await _collect_chip_candidates(
        page, "Cowork" if mode == "cowork" else "Chat"
    )
    return {
        "ok": False,
        "step": "chip_missing",
        "wanted": mode,
        "before": before,
        "compose_mode_fingerprint": fp,
        "candidates": candidates,
    }


async def _try_click_compose_chip(page, label: str) -> str | None:
    """Click Chat/Cowork chip if visible. Prefer Surface radiogroup; fallback unscoped."""
    # Sidebar owns aria-label="Mode" (Home/Code) — compose toggle is Surface + radio.
    scoped = page.get_by_role("radiogroup", name="Surface").get_by_role(
        "radio", name=re.compile(rf"^{re.escape(label)}$", re.I)
    )
    if await scoped.count():
        btn = scoped.first
        if await btn.is_visible():
            box = await btn.bounding_box()
            if box and not (
                box["width"] < 20 or box["width"] > 220 or box["height"] < 14
            ):
                await _scroll_click(btn)
                return "playwright_surface"

    for name in (label, label.lower(), label.upper()):
        for loc in (
            page.get_by_role("tab", name=re.compile(rf"^{re.escape(name)}$", re.I)),
            page.get_by_role("radio", name=re.compile(rf"^{re.escape(name)}$", re.I)),
            page.get_by_role("button", name=re.compile(rf"^{re.escape(name)}$", re.I)),
            page.get_by_text(name, exact=True),
        ):
            if not await loc.count():
                continue
            btn = loc.first
            if not await btn.is_visible():
                continue
            box = await btn.bounding_box()
            if box and (box["width"] < 20 or box["width"] > 220 or box["height"] < 14):
                continue
            await _scroll_click(btn)
            return "playwright"
    box = await _chip_center(page, label)
    if box:
        await page.mouse.click(box["x"], box["y"])
        return "mouse"
    return None


async def _poll_for_chip_or_attest(
    page,
    mode: ComposeMode,
    label: str,
    before: dict[str, Any],
    *,
    timeout_s: float = _CHIP_POLL_TIMEOUT_S,
    poll_ms: int = _POLL_MS,
) -> dict[str, Any]:
    """Poll fingerprint + chip presence before ``chip_missing`` (cold-compose hydrate)."""
    elapsed = 0.0
    limit_ms = int(timeout_s * 1000)
    last_fp = before
    while elapsed < limit_ms:
        await page.wait_for_timeout(poll_ms)
        elapsed += poll_ms
        last_fp = await compose_mode_fingerprint(page)
        if last_fp.get("mode") == mode:
            return {
                "ok": True,
                "step": f"already_{mode}",
                "before": before,
                "after": last_fp,
                "polled_ms": elapsed,
            }
        clicked_via = await _try_click_compose_chip(page, label)
        if clicked_via:
            return {"clicked": True, "via": clicked_via, "fingerprint": last_fp}
    return {"ok": False, "fingerprint": last_fp, "elapsed_ms": elapsed}


async def select_compose_mode(page, mode: ComposeMode) -> dict[str, Any]:
    """Toggle Chat/Cowork segmented control on ``/new`` (pre-submit)."""
    label = "Cowork" if mode == "cowork" else "Chat"
    before = await compose_mode_fingerprint(page)
    if before.get("mode") == mode:
        return {"ok": True, "step": f"already_{mode}", "before": before, "after": before}

    clicked_via = await _try_click_compose_chip(page, label)

    if not clicked_via:
        poll = await _poll_for_chip_or_attest(page, mode, label, before)
        if poll.get("ok"):
            return poll
        if poll.get("clicked"):
            clicked_via = poll["via"]
        else:
            return await _chip_missing_payload(page, mode=mode, before=before)

    if mode == "cowork" and label == "Cowork":
        after_probe = await compose_mode_fingerprint(page)
        if after_probe.get("mode") != "cowork" and not after_probe.get("approval"):
            js = await page.evaluate(
                """() => {
                  const hits = [];
                  for (const el of document.querySelectorAll('button,[role=button],[role=radio],span,div')) {
                    if ((el.innerText || '').trim() !== 'Cowork') continue;
                    if (!el.offsetParent) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 10 || r.height < 10) continue;
                    el.click();
                    hits.push({w: r.width, h: r.height, y: r.y});
                  }
                  return hits;
                }"""
            )
            if js:
                clicked_via = "js_brute"

    attest = await await_compose_attest(page, mode, timeout_s=8.0)
    after = attest.get("fingerprint") or await compose_mode_fingerprint(page)
    ok = bool(attest.get("ok"))
    return {
        "ok": ok,
        "step": f"selected_{mode}" if ok else f"select_{mode}_no_attest",
        "before": before,
        "after": after,
        "via": clicked_via,
        "attest": attest,
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


async def ensure_chat_compose(page) -> dict[str, Any]:
    """Select Chat mode on ``/new`` compose (operator-gated only).

    Call after landing on ``https://claude.ai/new``, before model pick / send.
    Requires explicit operator override (``--chat`` / ``chat_compose=true``).
    """
    mode = await select_compose_mode(page, "chat")
    return {
        "ok": bool(mode.get("ok")),
        "step": "chat_compose",
        "mode": mode,
    }


async def ensure_cowork_auto(page) -> dict[str, Any]:
    """Select Cowork mode and Automatically approve (>> Auto).

    Default on bare ``/new`` for automated CDP (friction 25051). Call after
    landing on ``https://claude.ai/new``, before model pick / send. No-op-ish
    on Project shells that lack the Chat/Cowork chips (returns
    ``chip_missing`` — callers may continue without failing hard).

    One bounded retry on approval-only failure — Cowork attest can succeed
    while the Manual→Auto menu click flakes (b7ea437d / 10:13 Manual fingerprint).
    """
    mode = await select_compose_mode(page, "cowork")
    if not mode.get("ok"):
        return {"ok": False, "step": "cowork", "mode": mode}
    approval = await set_approval_mode(page, "auto")
    if not approval.get("ok"):
        await page.wait_for_timeout(800)
        approval = await set_approval_mode(page, "auto")
        approval["retried"] = True
    return {
        "ok": bool(approval.get("ok")),
        "step": "cowork_auto",
        "mode": mode,
        "approval": approval,
    }
