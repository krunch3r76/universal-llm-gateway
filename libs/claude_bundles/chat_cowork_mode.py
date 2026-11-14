"""claude.ai compose-mode helpers — Chat/Cowork toggle + approval.

Verified on Jupiter CDP ask profile (:9223) 2026-07-16:

- Chat ↔ Cowork chips flip document title ``New chat`` ↔ ``New task``.
- Cowork exposes approval control (aria ``Manually approve`` /
  ``Automatically approve`` / skip).
- Menu radios: Manually approve | Automatically approve | Skip all approvals.
- Cowork + Skip all approvals default on bare ``/new`` (friction 25051).
- Chat via ``ensure_chat_compose`` — **operator-gated only** until dogfood passes.
- Toggle repair + poll-until-attest (friction 25052 — dual-primary Q1 bind).

a:31319 (2026-08-30): the approval chip can render with **no aria-label at
all** — a live census found a bare ``<button>`` reading just ``"Auto"``. The
old aria-only assumption above is no longer guaranteed; ``approval_label``
(compose_attest) and the short-form regex alternation below cover the
aria-less short-text shape alongside the original sentence-form aria.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from claude_bundles.compose_attest import (
    _POLL_MS,
    approval_label,
    await_compose_attest,
    compose_mode_fingerprint,
)
from claude_bundles.compose_chip_probe import (
    collect_approval_candidates,
    collect_chip_candidates,
    collect_radiogroup_evidence,
    compose_readiness_attestation,
    compose_ui_drift_detected,
    try_click_compose_chip,
    ui_census_from_radiogroup_evidence,
)

# Harvest mints sync_restart for cdp_ask when this module lands (ensure_cowork_auto path).
CONSUMERS: tuple[str, ...] = ("cdp_ask",)

_CHIP_POLL_TIMEOUT_S = 8.0

ApprovalMode = Literal["auto", "manual", "skip"]
ComposeMode = Literal["chat", "cowork"]
PermissionMode = Literal["Auto", "Manual"]

DEFAULT_QUOTA_NEAR_THRESHOLD = 95

_QUOTA_WEEKLY_PCT_RE = re.compile(r"(\d{1,3})\s*%\s+of\s+your\s+weekly\s+limit", re.I)

_PERMISSION_BUTTON_RE = re.compile(r"^(Manual|Auto)$", re.I)
_PERMISSION_ITEM = {
    "Auto": re.compile(r"^Auto\b", re.I),
    "Manual": re.compile(r"^Manual\b", re.I),
}

# Short-form alternation (a:31319) — chip can render aria-less with just the
# bare state name as its button text.
_APPROVAL_ARIA = {
    "auto": re.compile(r"Automatically approve|^Auto$", re.I),
    "manual": re.compile(r"Manually approve|^Manual$", re.I),
    "skip": re.compile(r"Skip all approvals|Never pause|^Skip$", re.I),
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

# Live confirm (Jupiter /new, 2026-09-29): the Skip menuitemradio does not
# change the chip. It opens role=dialog "Skip all approvals?" whose
# "Yes, continue" button is what sets the chip text to Skip.
_SKIP_CONFIRM_DIALOG_RE = re.compile(r"Skip all approvals\?", re.I)
_SKIP_CONFIRM_BUTTON_RE = re.compile(r"^Yes, continue$", re.I)

_APPROVAL_MENU_OPEN_POLL_MS = 400
_APPROVAL_MENU_OPEN_TIMEOUT_MS = 2500


def exclusive_radio_text_match(text: str, token: str) -> bool:
    """True iff ``text`` names ``token`` and no sibling approval label.

    Parent menu groups concatenate Manual+Auto copy; role=name matching those
    groups mis-clicks (friction 24610). Exclusive radios pass; groups fail.
    """
    if not re.search(re.escape(token), text, re.I):
        return False
    others = [o for o in _APPROVAL_RADIO_ALL if o.lower() != token.lower()]
    return not any(re.search(re.escape(o), text, re.I) for o in others)


async def _approval_menu_probe(page) -> dict[str, Any]:
    """Count visible approval menu rows across menuitemradio/menuitem/option."""
    raw = await page.evaluate(
        """() => {
          const pat = /approve|manual|skip|^auto$/i;
          const roles = ['menuitemradio', 'menuitem', 'option', 'radio'];
          const items = [];
          for (const role of roles) {
            for (const el of document.querySelectorAll(`[role="${role}"]`)) {
              if (!el.offsetParent) continue;
              const t = (el.innerText || '').replace(/\\s+/g, ' ');
              if (!pat.test(t)) continue;
              items.push({role, text: t.slice(0, 80)});
            }
          }
          return {count: items.length, items: items.slice(0, 12)};
        }"""
    )
    if not isinstance(raw, dict):
        return {"count": 0, "items": []}
    return {
        "count": int(raw.get("count") or 0),
        "items": list(raw.get("items") or []),
    }


async def _wait_approval_menu(page) -> dict[str, Any]:
    """Poll after chip click until ≥2 approval rows or timeout."""
    elapsed = 0
    last = await _approval_menu_probe(page)
    while elapsed < _APPROVAL_MENU_OPEN_TIMEOUT_MS:
        if last.get("count", 0) >= 2:
            return {"ok": True, "menu": last, "polled_ms": elapsed}
        await page.wait_for_timeout(_APPROVAL_MENU_OPEN_POLL_MS)
        elapsed += _APPROVAL_MENU_OPEN_POLL_MS
        last = await _approval_menu_probe(page)
    return {"ok": False, "menu": last, "polled_ms": elapsed}


async def _approval_chip_snapshot(page) -> dict[str, Any]:
    """Chip attrs for a failure payload (same size window as the JS opener)."""
    raw = await page.evaluate(
        """() => {
          const re = /^(auto|manual|skip)$/i;
          let best = null;
          for (const el of document.querySelectorAll('button, [role="button"]')) {
            const text = (el.innerText || '').trim();
            if (!re.test(text) || !el.offsetParent) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 28 || r.width > 140 || r.height < 16 || r.height > 48) {
              continue;
            }
            if (!best || r.width < best.w) best = {el, w: r.width};
          }
          if (!best) return {found: false};
          const el = best.el;
          return {
            found: true,
            text: (el.innerText || '').trim(),
            outerHTML: (el.outerHTML || '').slice(0, 600),
            attrs: {
              'aria-expanded': el.getAttribute('aria-expanded'),
              'aria-haspopup': el.getAttribute('aria-haspopup'),
              'data-state': el.getAttribute('data-state'),
              'aria-controls': el.getAttribute('aria-controls'),
              id: el.id || '',
            },
          };
        }"""
    )
    return raw if isinstance(raw, dict) else {"found": False}


async def _confirm_skip_dialog(page) -> dict[str, Any]:
    """Click ``Yes, continue`` if the Skip-all confirm dialog is up.

    No dialog and an already-Skip chip is success (older UI, or the row
    click itself committed). A dialog without that button fails closed —
    Cancel and Close are never clicked.
    """
    elapsed = 0
    while elapsed <= 1200:
        fp = await compose_mode_fingerprint(page)
        if fp.get("approval") and _APPROVAL_ARIA["skip"].search(approval_label(fp)):
            return {"confirmed": False, "already_skip": True, "polled_ms": elapsed}
        dialog = page.get_by_role("dialog").filter(has_text=_SKIP_CONFIRM_DIALOG_RE)
        if await dialog.count() and await dialog.first.is_visible():
            yes = dialog.get_by_role("button", name=_SKIP_CONFIRM_BUTTON_RE)
            if await yes.count() == 0:
                try:
                    text = (await dialog.first.inner_text())[:300]
                except Exception:
                    text = ""
                return {
                    "confirmed": False,
                    "ok": False,
                    "step": "skip_confirm_missing",
                    "dialog_text": text,
                    "polled_ms": elapsed,
                }
            try:
                await yes.first.click(timeout=5000)
            except Exception as exc:
                return {
                    "confirmed": False,
                    "ok": False,
                    "step": "skip_confirm_unattained",
                    "error": f"{type(exc).__name__}: {exc}",
                    "polled_ms": elapsed,
                }
            return {"confirmed": True, "polled_ms": elapsed}
        await page.wait_for_timeout(200)
        elapsed += 200
    return {"confirmed": False, "dialog": False, "polled_ms": elapsed}


async def _click_approval_chip_js(page) -> bool:
    """Click the compact Manual/Auto/Skip chip (aria-less production shape)."""
    return bool(
        await page.evaluate(
            """() => {
          const re = /^(auto|manual|skip)$/i;
          let best = null;
          for (const el of document.querySelectorAll('button, [role="button"]')) {
            const text = (el.innerText || '').trim();
            if (!re.test(text)) continue;
            if (!el.offsetParent) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 28 || r.width > 140 || r.height < 16 || r.height > 48) {
              continue;
            }
            if (!best || r.width < best.w) best = {el, w: r.width};
          }
          if (!best) return false;
          best.el.click();
          return true;
        }"""
        )
    )


async def _chip_missing_payload(
    page,
    *,
    mode: ComposeMode,
    before: dict[str, Any],
    click_probe: dict[str, Any] | None = None,
    polled_ms: float = 0,
) -> dict[str, Any]:
    """Failure payload with census + click-path gate rejects (falsifier evidence)."""
    fp = await compose_mode_fingerprint(page)
    label = "Cowork" if mode == "cowork" else "Chat"
    candidates = await collect_chip_candidates(page, label)
    probe = dict(click_probe or {})
    rg = await collect_radiogroup_evidence(page)
    if "radiogroup_names" not in probe:
        probe.update(rg)
    ui_census = ui_census_from_radiogroup_evidence(rg)
    ui_drift = compose_ui_drift_detected(rg)
    step = "ui_drift" if ui_drift else "chip_missing"
    out: dict[str, Any] = {
        "ok": False,
        "step": step,
        "wanted": mode,
        "before": before,
        "compose_mode_fingerprint": fp,
        "candidates": candidates,
        "click_probe": probe,
        "ui_census": ui_census,
        "surface_radiogroup_count": int(probe.get("surface_radiogroup_count") or 0),
        "radiogroup_names": list(probe.get("radiogroup_names") or []),
        "gate_rejects": list(probe.get("gate_rejects") or []),
        "polled_ms": polled_ms,
    }
    if ui_drift:
        out["failure_class"] = "ui_drift"
    return out


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
    last_probe: dict[str, Any] = {}
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
        if mode == "chat" and (last_fp.get("compose_readiness") or {}).get("ready"):
            return {
                "ok": True,
                "step": "already_chat",
                "before": before,
                "after": last_fp,
                "polled_ms": elapsed,
                "compose_readiness": last_fp.get("compose_readiness"),
            }
        clicked_via, last_probe = await try_click_compose_chip(page, label)
        if clicked_via:
            return {
                "clicked": True,
                "via": clicked_via,
                "fingerprint": last_fp,
                "click_probe": last_probe,
                "polled_ms": elapsed,
            }
    return {
        "ok": False,
        "fingerprint": last_fp,
        "elapsed_ms": elapsed,
        "click_probe": last_probe,
    }


async def select_compose_mode(page, mode: ComposeMode) -> dict[str, Any]:
    """Toggle Chat/Cowork segmented control on ``/new`` (pre-submit)."""
    label = "Cowork" if mode == "cowork" else "Chat"
    before = await compose_mode_fingerprint(page)
    if before.get("mode") == mode:
        rg = await collect_radiogroup_evidence(page)
        return {
            "ok": True,
            "step": f"already_{mode}",
            "before": before,
            "after": before,
            "click_probe": rg,
            "surface_radiogroup_count": rg["surface_radiogroup_count"],
            "radiogroup_names": rg["radiogroup_names"],
            "gate_rejects": [],
        }
    if mode == "chat" and (before.get("compose_readiness") or {}).get("ready"):
        attest = await await_compose_attest(
            page, mode, timeout_s=8.0, require_auto=False
        )
        after = attest.get("fingerprint") or before
        return {
            "ok": bool(attest.get("ok")),
            "step": "selected_chat" if attest.get("ok") else "select_chat_no_attest",
            "before": before,
            "after": after,
            "via": "compose_readiness",
            "attest": attest,
            "compose_readiness": before.get("compose_readiness"),
            "click_probe": await collect_radiogroup_evidence(page),
            "gate_rejects": [],
        }

    clicked_via, click_probe = await try_click_compose_chip(page, label)
    polled_ms = 0.0

    if not clicked_via:
        poll = await _poll_for_chip_or_attest(page, mode, label, before)
        if poll.get("ok"):
            return poll
        if poll.get("clicked"):
            clicked_via = poll["via"]
            click_probe = poll.get("click_probe") or click_probe
            polled_ms = float(poll.get("polled_ms") or 0)
        else:
            return await _chip_missing_payload(
                page,
                mode=mode,
                before=before,
                click_probe=poll.get("click_probe") or click_probe,
                polled_ms=float(poll.get("elapsed_ms") or 0),
            )

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

    attest = await await_compose_attest(page, mode, timeout_s=8.0, require_auto=False)
    after = attest.get("fingerprint") or await compose_mode_fingerprint(page)
    ok = bool(attest.get("ok"))
    return {
        "ok": ok,
        "step": f"selected_{mode}" if ok else f"select_{mode}_no_attest",
        "before": before,
        "after": after,
        "via": clicked_via,
        "attest": attest,
        "click_probe": click_probe,
        "surface_radiogroup_count": int(
            (click_probe or {}).get("surface_radiogroup_count") or 0
        ),
        "radiogroup_names": list((click_probe or {}).get("radiogroup_names") or []),
        "gate_rejects": list((click_probe or {}).get("gate_rejects") or []),
        "polled_ms": polled_ms,
    }


async def _open_approval_menu(page) -> dict[str, Any]:
    """Click current approval chip (Manual/Auto/Skip) and confirm a menu opened.

    a:31319 — the chip's aria-label can be entirely absent, and a click
    landing on *some* button is not proof the approval dropdown opened (the
    click might land on an unrelated aria-less control). Verify approval menu
    rows (``menuitemradio`` / ``menuitem`` / ``option``) actually appeared
    before reporting success; on exhaustion, dump every Auto/approve/manual/
    skip-like candidate so the next occurrence is a one-look diagnosis instead
    of a fresh investigation.
    """
    for aria_re in (
        _APPROVAL_ARIA["auto"],
        _APPROVAL_ARIA["manual"],
        _APPROVAL_ARIA["skip"],
    ):
        loc = page.get_by_label(aria_re)
        if await loc.count():
            await loc.first.click(force=True)
            waited = await _wait_approval_menu(page)
            if waited.get("ok"):
                return {
                    "ok": True,
                    "opened_via": "aria",
                    "pattern": aria_re.pattern,
                    "menu": waited.get("menu"),
                    "polled_ms": waited.get("polled_ms"),
                }
    if await _click_approval_chip_js(page):
        waited = await _wait_approval_menu(page)
        if waited.get("ok"):
            return {
                "ok": True,
                "opened_via": "js_chip",
                "menu": waited.get("menu"),
                "polled_ms": waited.get("polled_ms"),
            }
    # Fallback: visible Manual/Auto/Skip button text (chip may carry no aria).
    for pat in (r"^Manual\b", r"^Auto\b", r"^Skip\b"):
        loc = page.locator('button, [role="button"]').filter(
            has_text=re.compile(pat, re.I)
        )
        if await loc.count():
            await loc.first.click(force=True)
            waited = await _wait_approval_menu(page)
            if waited.get("ok"):
                return {
                    "ok": True,
                    "opened_via": "text",
                    "pattern": pat,
                    "menu": waited.get("menu"),
                    "polled_ms": waited.get("polled_ms"),
                }
    menu = await _approval_menu_probe(page)
    return {
        "ok": False,
        "step": "approval_control_missing",
        "menu_probe": menu,
        "candidates": await collect_approval_candidates(page),
    }


async def set_approval_mode(page, mode: ApprovalMode = "skip") -> dict[str, Any]:
    """Click the Cowork approval radio. Default selects Skip all approvals.

    Requires Cowork compose chrome. ``auto`` and ``manual`` remain clickable
    menu radios; the ship path uses this skip default.
    """
    before = await compose_mode_fingerprint(page)
    wanted_aria = _APPROVAL_ARIA[mode]
    # approval_label falls back to the chip's short text when aria is absent
    # (a:31319) — without this, an already-attested aria-less "Auto" chip
    # would fall through to _open_approval_menu and click needlessly.
    if before.get("approval") and wanted_aria.search(approval_label(before)):
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
          const roles = ['menuitemradio', 'menuitem', 'option'];
          const radios = [];
          for (const role of roles) {
            radios.push(...document.querySelectorAll(`[role="${role}"]`));
          }
          const all = ['Manually approve', 'Automatically approve', 'Skip all approvals'];
          const others = all.filter((x) => x.toLowerCase() !== token.toLowerCase());
          for (const el of radios) {
            const t = (el.innerText || '').replace(/\\s+/g, ' ');
            if (!exclusiveRadioTextMatch(t, token, others)) continue;
            el.click();
            return {ok: true, text: t.slice(0, 120), role: el.getAttribute('role') || ''};
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
            item = page.get_by_role("option", name=menu_re)
        if await item.count() == 0:
            items = list((opened.get("menu") or {}).get("items") or [])
            no_skip = (
                mode == "skip"
                and bool(items)
                and not any(
                    re.search(r"skip", str(row.get("text") or ""), re.I)
                    for row in items
                )
            )
            return {
                "ok": False,
                "step": "skip_option_absent" if no_skip else "menu_item_missing",
                "wanted": mode,
                "opened": opened,
                "before": before,
                "exclusive": exclusive,
                "rows": items,
                "candidates": await collect_approval_candidates(page),
                "chip": await _approval_chip_snapshot(page),
            }
        await item.first.click(force=True)
    confirm: dict[str, Any] | None = None
    if mode == "skip":
        confirm = await _confirm_skip_dialog(page)
        if confirm.get("ok") is False:
            return {
                "ok": False,
                "step": str(confirm.get("step") or "skip_confirm_unattained"),
                "opened": opened,
                "before": before,
                "confirm": confirm,
                "candidates": await collect_approval_candidates(page),
                "chip": await _approval_chip_snapshot(page),
            }
    await page.wait_for_timeout(1200)
    after = await compose_mode_fingerprint(page)
    ok = bool(after.get("approval") and wanted_aria.search(approval_label(after)))
    result = {
        "ok": ok,
        "step": f"selected_{mode}" if ok else f"select_{mode}_no_attest",
        "opened": opened,
        "before": before,
        "after": after,
    }
    if confirm is not None:
        result["confirm"] = confirm
    if not ok:
        result["candidates"] = await collect_approval_candidates(page)
        result["chip"] = await _approval_chip_snapshot(page)
    return result


async def _permission_menu_probe(page) -> dict[str, Any]:
    """Count visible Permission-mode rows (Manual/Auto without legacy approve copy)."""
    raw = await page.evaluate(
        """() => {
          const roles = ['menuitemradio', 'menuitem', 'option'];
          const items = [];
          for (const role of roles) {
            for (const el of document.querySelectorAll(`[role="${role}"]`)) {
              if (!el.offsetParent) continue;
              const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
              if (!/^(manual|auto)\\b/i.test(t)) continue;
              if (/approve/i.test(t)) continue;
              items.push({
                role,
                text: t.slice(0, 100),
                aria_checked: el.getAttribute('aria-checked') || '',
              });
            }
          }
          return {count: items.length, items: items.slice(0, 8)};
        }"""
    )
    if not isinstance(raw, dict):
        return {"count": 0, "items": []}
    return {
        "count": int(raw.get("count") or 0),
        "items": list(raw.get("items") or []),
    }


async def _wait_permission_menu(page) -> dict[str, Any]:
    elapsed = 0
    last = await _permission_menu_probe(page)
    while elapsed < _APPROVAL_MENU_OPEN_TIMEOUT_MS:
        if last.get("count", 0) >= 2:
            return {"ok": True, "menu": last, "polled_ms": elapsed}
        await page.wait_for_timeout(_APPROVAL_MENU_OPEN_POLL_MS)
        elapsed += _APPROVAL_MENU_OPEN_POLL_MS
        last = await _permission_menu_probe(page)
    return {"ok": False, "menu": last, "polled_ms": elapsed}


async def _permission_mode_button_label(page) -> dict[str, Any]:
    """Read the compact Manual/Auto permission-mode trigger, if present."""
    raw = await page.evaluate(
        """() => {
          const re = /^(manual|auto)$/i;
          function pickIn(root) {
            let best = null;
            for (const el of root.querySelectorAll('button, [role="button"]')) {
              const text = (el.innerText || '').trim();
              if (!re.test(text) || !el.offsetParent) continue;
              const r = el.getBoundingClientRect();
              if (r.width < 28 || r.width > 160 || r.height < 16 || r.height > 52) {
                continue;
              }
              if (!best || r.width < best.w) best = {el, w: r.width, text};
            }
            return best;
          }
          let lookup = 'viewport_fallback';
          let best = null;
          const chat = document.querySelector('[data-testid="chat-input"]');
          if (chat) {
            const root =
              chat.closest('form, main, section, [class*="compose"]')
              || chat.parentElement;
            if (root) {
              best = pickIn(root);
              if (best) lookup = 'chat_input_region';
            }
          }
          if (!best) {
            best = pickIn(document.body);
            lookup = 'viewport_fallback';
          }
          if (!best) return {found: false, permission_lookup: lookup};
          return {
            found: true,
            text: best.text,
            aria: best.el.getAttribute('aria-label') || '',
            permission_lookup: lookup,
          };
        }"""
    )
    return raw if isinstance(raw, dict) else {"found": False}


async def _click_permission_mode_button(page) -> bool:
    clicked = await page.evaluate(
        """() => {
          const re = /^(manual|auto)$/i;
          function pickIn(root) {
            let best = null;
            for (const el of root.querySelectorAll('button, [role="button"]')) {
              const text = (el.innerText || '').trim();
              if (!re.test(text) || !el.offsetParent) continue;
              const r = el.getBoundingClientRect();
              if (r.width < 28 || r.width > 160 || r.height < 16 || r.height > 52) {
                continue;
              }
              if (!best || r.width < best.w) best = {el, w: r.width};
            }
            return best;
          }
          let best = null;
          const chat = document.querySelector('[data-testid="chat-input"]');
          if (chat) {
            const root =
              chat.closest('form, main, section, [class*="compose"]')
              || chat.parentElement;
            if (root) best = pickIn(root);
          }
          if (!best) best = pickIn(document.body);
          if (!best) return false;
          best.el.click();
          return true;
        }"""
    )
    return bool(clicked)


async def _open_permission_menu(page) -> dict[str, Any]:
    """Open the Permission mode menu from the Manual/Auto chip."""
    btn = await _permission_mode_button_label(page)
    if btn.get("found") and await _click_permission_mode_button(page):
        waited = await _wait_permission_menu(page)
        if waited.get("ok"):
            return {
                "ok": True,
                "opened_via": "permission_chip",
                "button_before": btn,
                "menu": waited.get("menu"),
                "polled_ms": waited.get("polled_ms"),
            }
    for pat in (r"^Manual\b", r"^Auto\b"):
        loc = page.locator('button, [role="button"]').filter(
            has_text=re.compile(pat, re.I)
        )
        if await loc.count():
            await loc.first.click(force=True)
            waited = await _wait_permission_menu(page)
            if waited.get("ok"):
                return {
                    "ok": True,
                    "opened_via": "text",
                    "pattern": pat,
                    "menu": waited.get("menu"),
                    "polled_ms": waited.get("polled_ms"),
                }
    rg = await collect_radiogroup_evidence(page)
    return {
        "ok": False,
        "step": "permission_control_missing",
        "button": btn,
        "menu_probe": await _permission_menu_probe(page),
        "ui_census": ui_census_from_radiogroup_evidence(rg),
        "failure_class": "ui_drift",
    }


def _permission_attested(button: dict[str, Any], mode: PermissionMode) -> bool:
    text = str(button.get("text") or "")
    return bool(_PERMISSION_BUTTON_RE.match(text) and text.lower() == mode.lower())


async def ensure_permission_mode(page, mode: PermissionMode = "Auto") -> dict[str, Any]:
    """Select Permission mode Auto or Manual on bare ``/new`` (compose v2).

      Opens the permission-mode button, chooses the menu row, and read-backs the
      button label plus the checked menu item. Missing chrome fails closed with
    ``failure_class=ui_drift`` and ``ui_census``.
    """
    before_btn = await _permission_mode_button_label(page)
    if before_btn.get("found") and _permission_attested(before_btn, mode):
        menu = await _permission_menu_probe(page)
        return {
            "ok": True,
            "step": f"already_{mode.lower()}",
            "button_label": before_btn.get("text"),
            "before": before_btn,
            "after": before_btn,
            "menu_probe": menu,
        }

    opened = await _open_permission_menu(page)
    if not opened.get("ok"):
        return {**opened, "wanted": mode, "before": before_btn}

    item_re = _PERMISSION_ITEM[mode]
    picked = await page.evaluate(
        """(modePat) => {
          const re = new RegExp(modePat, 'i');
          const roles = ['menuitemradio', 'menuitem', 'option'];
          for (const role of roles) {
            for (const el of document.querySelectorAll(`[role="${role}"]`)) {
              const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
              if (!re.test(t) || /approve/i.test(t)) continue;
              if (/manual/i.test(t) && /auto/i.test(t)) continue;
              el.click();
              return {
                ok: true,
                text: t.slice(0, 120),
                aria_checked: el.getAttribute('aria-checked') || '',
                role,
              };
            }
          }
          return {ok: false};
        }""",
        item_re.pattern,
    )
    if not isinstance(picked, dict) or not picked.get("ok"):
        rg = await collect_radiogroup_evidence(page)
        return {
            "ok": False,
            "step": "permission_menu_item_missing",
            "wanted": mode,
            "opened": opened,
            "before": before_btn,
            "menu_probe": await _permission_menu_probe(page),
            "ui_census": ui_census_from_radiogroup_evidence(rg),
            "failure_class": "ui_drift",
        }

    await page.wait_for_timeout(400)
    after_btn = await _permission_mode_button_label(page)
    menu_after = await _permission_menu_probe(page)
    checked = [
        row
        for row in menu_after.get("items") or []
        if str(row.get("aria_checked") or "").lower() == "true"
        and item_re.search(str(row.get("text") or ""))
    ]
    ok = _permission_attested(after_btn, mode) or bool(checked)
    out: dict[str, Any] = {
        "ok": ok,
        "step": f"selected_{mode.lower()}"
        if ok
        else f"select_{mode.lower()}_no_attest",
        "wanted": mode,
        "opened": opened,
        "before": before_btn,
        "after": after_btn,
        "button_label": after_btn.get("text"),
        "selected_item": picked,
        "checked_items": checked,
        "menu_probe": menu_after,
    }
    if not ok:
        rg = await collect_radiogroup_evidence(page)
        ui_census = ui_census_from_radiogroup_evidence(rg)
        lookup = str(after_btn.get("permission_lookup") or "")
        if lookup:
            ui_census = {**ui_census, "permission_lookup": lookup}
        out["ui_census"] = ui_census
        out["failure_class"] = "ui_drift"
    elif after_btn.get("permission_lookup") == "viewport_fallback":
        rg = await collect_radiogroup_evidence(page)
        out["ui_census"] = {
            **ui_census_from_radiogroup_evidence(rg),
            "permission_lookup": "viewport_fallback",
        }
    return out


async def read_quota_weekly_pct(page) -> dict[str, Any]:
    """Parse weekly-limit usage percentage when the banner is visible."""
    raw = await page.evaluate(
        """() => {
          const pat = /\\d{1,3}\\s*%\\s*of\\s*your\\s*weekly\\s*limit/i;
          for (const el of document.querySelectorAll(
            'div,span,p,[role=banner],[role=alert],aside'
          )) {
            const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
            if (!pat.test(t)) continue;
            if (!el.offsetParent) continue;
            return {found: true, text: t.slice(0, 240)};
          }
          return {found: false};
        }"""
    )
    if not isinstance(raw, dict) or not raw.get("found"):
        return {"pct": None, "banner_text": None}
    text = str(raw.get("text") or "")
    match = _QUOTA_WEEKLY_PCT_RE.search(text)
    pct = int(match.group(1)) if match else None
    return {"pct": pct, "banner_text": text}


_COMPOSE_MODEL_CHIP_JS = """() => {
  const PUA = /[\\uE000-\\uF8FF]/g;
  const strip = (s) => (s || '').replace(PUA, '').replace(/\\s+/g, ' ').trim();
  const familyRe = /^(Opus|Sonnet|Haiku|Fable)\\b/i;
  const effortRe = /\\b(low|medium|high|extra|max|extended)\\b/i;
  const permRe = /^(manual|auto)$/i;

  function composeRoot() {
    const chat = document.querySelector('[data-testid="chat-input"]');
    if (!chat) return null;
    return (
      chat.closest('form, main, section, [class*="compose"]') || chat.parentElement
    );
  }

  function candidateRow(el) {
    const r = el.getBoundingClientRect();
    return {
      text: (el.innerText || '').trim().slice(0, 120),
      aria_label: (el.getAttribute('aria-label') || '').slice(0, 120),
      data_testid: el.getAttribute('data-testid') || '',
      x: r.x,
      y: r.y,
      w: r.width,
      h: r.height,
      offset_parent: !!el.offsetParent,
    };
  }

  function chipBlob(aria, text) {
    const raw = strip(aria || text);
    if (!raw) return '';
    return raw.replace(/^Model:\\s*/i, '').trim();
  }

  function parsableModelLabel(label) {
    const blob = chipBlob('', label);
    if (!blob) return false;
    return familyRe.test(blob);
  }

  function modelLabelFrom(aria, text) {
    const blob = chipBlob(aria, text);
    if (!blob) return '';
    const m = blob.match(
      /^(Opus|Sonnet|Haiku|Fable)(?:\\s+(\\d+(?:\\.\\d+)?))?/i
    );
    if (!m) return blob.split(/\\n/)[0].trim();
    const ver = m[2] || '';
    return ver ? `${m[1]} ${ver}` : m[1];
  }

  function readTestidChip(el) {
    const aria = (el.getAttribute('aria-label') || '').trim();
    const rawText = (el.innerText || '').trim();
    const text = strip(rawText);
    const label = modelLabelFrom(aria, text);
    if (!parsableModelLabel(label)) return null;
    let effort = null;
    const em = (aria + '\\n' + rawText).match(effortRe);
    if (em) effort = em[1];
    return {label, effort};
  }

  function pickModel(root) {
    let best = null;
    for (const el of root.querySelectorAll('button, [role="button"]')) {
      if (!el.offsetParent) continue;
      const aria = (el.getAttribute('aria-label') || '').trim();
      const rawText = (el.innerText || '').trim();
      const text = strip(rawText);
      const blob = chipBlob(aria, text);
      if (!familyRe.test(blob)) continue;
      if (permRe.test(text) || permRe.test(strip(aria))) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 28 || r.width > 200 || r.height < 14 || r.height > 52) {
        continue;
      }
      if (!best || r.width < best.w) {
        const label = modelLabelFrom(aria, text);
        let effort = null;
        const effMatch = (aria + '\\n' + rawText).match(effortRe);
        if (effMatch) effort = effMatch[1];
        best = {el, w: r.width, label, effort, lookup: 'chat_input_region'};
      }
    }
    return best;
  }

  function pickEffort(root, skipEl) {
    for (const el of root.querySelectorAll('button, [role="button"], span')) {
      if (skipEl && el === skipEl) continue;
      if (!el.offsetParent) continue;
      const t = strip((el.innerText || '').trim());
      if (!/^(low|medium|high|extra|max|extended)$/i.test(t)) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 12 || r.width > 120 || r.height < 12 || r.height > 48) {
        continue;
      }
      return t;
    }
    return null;
  }

  const root = composeRoot();
  const compose_candidates = [];
  if (root) {
    for (const el of root.querySelectorAll('button, [role="button"]')) {
      compose_candidates.push(candidateRow(el));
    }
  }

  const testid = document.querySelector('[data-testid="model-selector-dropdown"]');
  let testid_probe = null;
  if (testid) {
    const st = getComputedStyle(testid);
    const r = testid.getBoundingClientRect();
    testid_probe = {
      outerHTML: (testid.outerHTML || '').slice(0, 2000),
      visibility: {
        display: st.display,
        visibility: st.visibility,
        opacity: st.opacity,
      },
      offset_parent: !!testid.offsetParent,
      bbox: {x: r.x, y: r.y, w: r.width, h: r.height},
    };
  }

  let model_label = '';
  let effort = null;
  let lookup = '';
  let chip_el = null;

  if (root) {
    const picked = pickModel(root);
    if (picked && parsableModelLabel(picked.label)) {
      model_label = picked.label;
      effort = picked.effort;
      lookup = picked.lookup;
      chip_el = picked.el;
    }
  }

  if (!model_label && testid && testid.offsetParent) {
    const hit = readTestidChip(testid);
    if (hit) {
      model_label = hit.label;
      effort = hit.effort;
      lookup = 'model_selector_testid';
      chip_el = testid;
    }
  }

  if (root && !effort) {
    effort = pickEffort(root, chip_el);
  }

  return {
    model_label,
    effort,
    lookup,
    model_chip_candidates: compose_candidates,
    model_chip_compose_candidates: compose_candidates,
    model_chip_testid_probe: testid_probe,
    compose_root: !!root,
  };
}"""

_EFFORT_CHIP_STANDALONE_JS = """() => {
  const strip = (s) => (s || '').replace(/[\\uE000-\\uF8FF]/g, '').trim();
  const chat = document.querySelector('[data-testid="chat-input"]');
  const root = chat
    ? chat.closest('form, main, section, [class*="compose"]') || chat.parentElement
    : document.body;
  if (!root) return '';
  for (const el of root.querySelectorAll('button, [role=button], span')) {
    const t = strip((el.innerText || '').trim());
    if (!/^(low|medium|high|extra|max|extended)$/i.test(t) || !el.offsetParent) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 12 || r.width > 120 || r.height < 12 || r.height > 48) continue;
    return t;
  }
  return '';
}"""


def _normalize_compose_effort(raw: str | None) -> str | None:
    text = str(raw or "").strip()
    return text or None


async def _open_compose_model_menu(page) -> None:
    """Open the model picker from testid dropdown or compose-region family chip."""
    from claude_bundles.chat_model_select import _open_picker

    btn = page.locator('[data-testid="model-selector-dropdown"]')
    if await btn.count():
        await _open_picker(page)
        return
    opened = await page.evaluate(
        """() => {
          const PUA = /[\\uE000-\\uF8FF]/g;
          const strip = (s) => (s || '').replace(PUA, '').replace(/\\s+/g, ' ').trim();
          const familyRe = /^(Opus|Sonnet|Haiku|Fable)\\b/i;
          const permRe = /^(manual|auto)$/i;
          const chat = document.querySelector('[data-testid="chat-input"]');
          if (!chat) return false;
          const root =
            chat.closest('form, main, section, [class*="compose"]') ||
            chat.parentElement;
          if (!root) return false;
          let best = null;
          for (const el of root.querySelectorAll('button, [role="button"]')) {
            if (!el.offsetParent) continue;
            const aria = strip(el.getAttribute('aria-label') || '');
            const text = strip((el.innerText || '').trim());
            const blob = aria || text;
            if (!familyRe.test(blob) || permRe.test(text)) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 28 || r.width > 200 || r.height < 14 || r.height > 52) {
              continue;
            }
            if (!best || r.width < best.w) best = {el, w: r.width};
          }
          if (!best) return false;
          best.el.click();
          return true;
        }"""
    )
    if opened:
        await page.wait_for_timeout(600)


async def _read_compose_effort_from_picker(page) -> str | None:
    """Read effort for the checked model row when it is not on the compact chip."""
    await _open_compose_model_menu(page)
    raw = await page.evaluate(
        """() => {
          const checked = document.querySelector(
            '[role=menuitemradio][aria-checked="true"]'
          );
          if (!checked) return '';
          return (
            checked.getAttribute('aria-label') || checked.innerText || ''
          ).trim();
        }"""
    )
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(200)
    if not raw:
        return None
    from claude_bundles.composer_skill_match import strip_pua

    text = strip_pua(str(raw))
    for token in ("extended", "extra", "max", "high", "medium", "low"):
        if re.search(rf"\b{re.escape(token)}\b", text, re.I):
            return token.title() if token != "extra" else "Extra"
    return None


async def _probe_compose_model_chip(page) -> dict[str, Any]:
    raw = await page.evaluate(_COMPOSE_MODEL_CHIP_JS)
    if not isinstance(raw, dict):
        return {
            "model_label": "",
            "effort": None,
            "lookup": "",
            "model_chip_candidates": [],
        }
    compose_candidates = list(
        raw.get("model_chip_compose_candidates")
        or raw.get("model_chip_candidates")
        or []
    )
    return {
        "model_label": str(raw.get("model_label") or "").strip(),
        "effort": _normalize_compose_effort(raw.get("effort")),
        "lookup": str(raw.get("lookup") or ""),
        "model_chip_candidates": compose_candidates,
        "model_chip_compose_candidates": compose_candidates,
        "model_chip_testid_probe": raw.get("model_chip_testid_probe"),
    }


async def _ui_census_with_model_chip(page) -> dict[str, Any]:
    rg = await collect_radiogroup_evidence(page)
    base = ui_census_from_radiogroup_evidence(rg)
    probe = await _probe_compose_model_chip(page)
    compose_candidates = (
        probe.get("model_chip_compose_candidates")
        or probe.get("model_chip_candidates")
        or []
    )
    base["model_chip_candidates"] = compose_candidates
    base["model_chip_compose_candidates"] = compose_candidates
    if probe.get("model_chip_testid_probe") is not None:
        base["model_chip_testid_probe"] = probe["model_chip_testid_probe"]
    if probe.get("lookup"):
        base["model_chip_lookup"] = probe["lookup"]
    return base


async def read_compose_model_chip(
    page, *, fill_effort_from_menu: bool = False
) -> dict[str, Any]:
    """Read model picker label and separate effort chip when present."""
    probe = await _probe_compose_model_chip(page)
    label = probe.get("model_label") or ""
    effort = probe.get("effort")
    if not label:
        from claude_bundles.chat_model_select import current_model_label

        label = (await current_model_label(page)).strip()
    if not effort:
        standalone = await page.evaluate(_EFFORT_CHIP_STANDALONE_JS)
        effort = _normalize_compose_effort(standalone)
    if fill_effort_from_menu and not effort:
        effort = await _read_compose_effort_from_picker(page)
    out: dict[str, Any] = {
        "model_label": label,
        "effort": effort,
    }
    if probe.get("lookup"):
        out["model_chip_lookup"] = probe["lookup"]
    return out


async def ensure_compose_model_chip(
    page, *, model_request: str, effort: str | None = None
) -> dict[str, Any]:
    """Assert or set model + effort via the compose model chip."""
    from claude_bundles.chat_model_match import label_satisfies_request
    from claude_bundles.chat_model_select import select_model

    before = await read_compose_model_chip(page, fill_effort_from_menu=bool(effort))
    label = before.get("model_label") or ""
    if not label:
        return {
            "ok": False,
            "step": "model_chip_missing",
            "before": before,
            "ui_census": await _ui_census_with_model_chip(page),
            "failure_class": "ui_drift",
        }
    if label_satisfies_request(label, model_request) and (
        effort is None or (before.get("effort") or "").lower() == effort.strip().lower()
    ):
        return {
            "ok": True,
            "step": "already_model",
            "before": before,
            "after": before,
            "requested": model_request,
        }
    picked = await select_model(page, model_request)
    after = await read_compose_model_chip(page, fill_effort_from_menu=bool(effort))
    ok = bool(picked.get("ok")) and label_satisfies_request(
        after.get("model_label") or "", model_request
    )
    if effort and (after.get("effort") or "").lower() != effort.strip().lower():
        ok = False
    result: dict[str, Any] = {
        "ok": ok,
        "step": "model_chip_set" if ok else "model_chip_mismatch",
        "requested": model_request,
        "before": before,
        "after": after,
        "pick": picked,
    }
    if not ok:
        result["ui_census"] = await _ui_census_with_model_chip(page)
        result["failure_class"] = "ui_drift"
    return result


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


async def ensure_cowork_auto(
    page,
    *,
    model_request: str | None = None,
    model_effort: str | None = None,
    quota_near_threshold: int = DEFAULT_QUOTA_NEAR_THRESHOLD,
) -> dict[str, Any]:
    """Bare ``/new`` compose v2: readiness + Permission Auto (+ optional model chip).

    Replaces the legacy Cowork + Skip-all path for the default CDP surface
    (a:43854). When ``compose_readiness`` attests Claude mode, sets Permission
    mode to Auto, records ``quota_weekly_pct``, and optionally asserts the model
    chip. Legacy Surface Chat/Cowork chrome is no longer selected on this path.
    """
    readiness = await compose_readiness_attestation(page)
    if not readiness.get("ready"):
        rg = await collect_radiogroup_evidence(page)
        ui_census = ui_census_from_radiogroup_evidence(rg)
        drift = compose_ui_drift_detected(rg)
        return {
            "ok": False,
            "step": "ui_drift" if drift else "compose_not_ready",
            "failure_class": "ui_drift" if drift else "toggle",
            "compose_readiness": readiness,
            "ui_census": ui_census,
            "surface_radiogroup_count": int(rg.get("surface_radiogroup_count") or 0),
            "radiogroup_names": list(rg.get("radiogroup_names") or []),
        }

    permission = await ensure_permission_mode(page, "Auto")
    quota = await read_quota_weekly_pct(page)
    out: dict[str, Any] = {
        "ok": True,
        "step": "compose_v2",
        "compose_readiness": readiness,
        "permission": permission,
        "quota_weekly_pct": quota.get("pct"),
        "quota_banner": quota,
    }
    if not permission.get("ok"):
        out["ok"] = False
        out["step"] = "permission_mode"
        out["failure_class"] = permission.get("failure_class") or "ui_drift"
        if permission.get("ui_census"):
            out["ui_census"] = permission["ui_census"]
        return out

    pct = quota.get("pct")
    if pct is not None and int(pct) >= int(quota_near_threshold):
        out["ok"] = False
        out["step"] = "quota_near"
        out["failure_class"] = "quota_near"
        return out

    if model_request:
        chip = await ensure_compose_model_chip(
            page,
            model_request=model_request,
            effort=model_effort,
        )
        out["model_chip"] = chip
        if not chip.get("ok"):
            out["ok"] = False
            out["step"] = "model_chip"
            out["failure_class"] = chip.get("failure_class") or "ui_drift"
            if chip.get("ui_census"):
                out["ui_census"] = chip["ui_census"]
    return out


async def ensure_approval_auto(page) -> dict[str, Any]:
    """Re-apply Skip all approvals when the approval chip exists.

    Used immediately before Start task / warm Send so model-picker or paste
    cannot ship Cowork+Manual or Cowork+Auto after ``ensure_cowork_auto``
    already attested on ``/new``. Skips when chrome is absent (Chat / Project
    shell). Does not toggle Cowork — callers that need the chip use
    ``ensure_cowork_auto``.
    """
    before = await compose_mode_fingerprint(page)
    if not before.get("approval"):
        return {
            "ok": True,
            "step": "approval_chrome_absent",
            "skipped": True,
            "before": before,
            "after": before,
        }
    approval = await set_approval_mode(page, "skip")
    if not approval.get("ok"):
        await page.wait_for_timeout(800)
        approval = await set_approval_mode(page, "skip")
        approval["retried"] = True
    return approval
