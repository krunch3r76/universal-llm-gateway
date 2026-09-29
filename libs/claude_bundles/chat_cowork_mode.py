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
    try_click_compose_chip,
)

# Harvest mints sync_restart for cdp_ask when this module lands (ensure_cowork_auto path).
CONSUMERS: tuple[str, ...] = ("cdp_ask",)

_CHIP_POLL_TIMEOUT_S = 8.0

ApprovalMode = Literal["auto", "manual", "skip"]
ComposeMode = Literal["chat", "cowork"]

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
    if "radiogroup_names" not in probe:
        probe.update(await collect_radiogroup_evidence(page))
    return {
        "ok": False,
        "step": "chip_missing",
        "wanted": mode,
        "before": before,
        "compose_mode_fingerprint": fp,
        "candidates": candidates,
        "click_probe": probe,
        "surface_radiogroup_count": int(probe.get("surface_radiogroup_count") or 0),
        "radiogroup_names": list(probe.get("radiogroup_names") or []),
        "gate_rejects": list(probe.get("gate_rejects") or []),
        "polled_ms": polled_ms,
    }


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
    """Select Cowork mode and Skip all approvals (>> Skip).

    Default on bare ``/new`` for automated CDP (friction 25051). Call after
    landing on ``https://claude.ai/new``, before model pick / send. No-op-ish
    on Project shells that lack the Chat/Cowork chips (returns
    ``chip_missing`` — callers may continue without failing hard).

    One bounded retry on approval-only failure — Cowork attest can succeed
    while the menu click to Skip all approvals flakes (b7ea437d / 10:13
    Manual fingerprint). Skip unattested fails closed. Auto and Manual are
    never accepted.
    """
    mode = await select_compose_mode(page, "cowork")
    if not mode.get("ok"):
        return {"ok": False, "step": "cowork", "mode": mode}
    approval = await set_approval_mode(page, "skip")
    if not approval.get("ok"):
        await page.wait_for_timeout(800)
        approval = await set_approval_mode(page, "skip")
        approval["retried"] = True
    if approval.get("ok"):
        return {
            "ok": True,
            "step": "cowork_auto",
            "mode": mode,
            "approval": approval,
        }
    after_fp = (
        approval.get("after")
        or approval.get("before")
        or await compose_mode_fingerprint(page)
    )
    step = (
        "skip_option_absent"
        if approval.get("step") == "skip_option_absent"
        else "cowork_skip_unattained"
    )
    return {
        "ok": False,
        "step": step,
        "mode": mode,
        "approval": approval,
        "fingerprint": after_fp,
    }


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
