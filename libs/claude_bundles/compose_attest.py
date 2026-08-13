"""Shared compose attestation — mode fingerprint, poll-until-attest, submit wait.

Dual-primary (friction 25051/25052): selector repair + poll-until-attest, not
poll-only. Chip toggle may attest Cowork while approval is still Manual.
Ship gate (Start task): Cowork requires aria ``Automatically approve`` —
``mode==cowork`` alone or any approval chip (Manual/Skip) is not success.
Chat success: ``mode==chat`` and approval is None. Project-shell rows without
chips are a named skip, not a silent Auto waiver on ``/new``.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from claude_bundles.chat_session_hygiene import in_active_chat

ComposeMode = Literal["chat", "cowork"]
SubmitStrategy = Literal["mode_locked", "live_discover"]

# AM-4 settle contract — tuned against DOM sample (path-sim-cdp-cse-warm-followup-submit-dom-sample.md):
# warm CSE follow-up uses renamed Send (not Start task); control is present after composer refocus.
_WARM_SUBMIT_SETTLE_MS = 300
_WARM_SUBMIT_REFOCUS = True

# Positive submit heuristics — cite DOM sample: warm CSE exposes aria-label "Send", not "Send message".
_POSITIVE_SUBMIT_RE = re.compile(
    r"^(send(\s+message)?|submit|continue|start task)$",
    re.I,
)
# Exclude non-submit composer chrome — cite DOM sample sibling controls.
_EXCLUDE_SUBMIT_RE = re.compile(
    r"(model|approve|approval|attach|attachment|upload|stop|generating|"
    r"menu|more options|sidebar|settings|tools|skill|voice|dictate|"
    r"thinking|expand|collapse|copy|share|regenerate|retry|cancel|close|"
    r"new chat|new task|cowork|chat mode|effort|extended|high|extra|max)",
    re.I,
)

_POLL_MS = 400
_AUTO_ARIA_RE = re.compile(r"Automatically approve", re.I)
_MANUAL_ARIA_RE = re.compile(r"Manually approve", re.I)
_SKIP_ARIA_RE = re.compile(r"Skip all approvals|Never pause", re.I)
_SUBMIT_ROLE = {
    "cowork": re.compile(r"start task", re.I),
    "chat": re.compile(r"send message", re.I),
}
_SUBMIT_ARIA = {
    "cowork": "Start task",
    "chat": "Send",
}


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


def _approval_aria(fp: dict[str, Any]) -> str:
    """Return the approval chip aria-label, or empty when chrome is absent."""
    approval = fp.get("approval")
    if not isinstance(approval, dict):
        return ""
    return str(approval.get("aria") or "")


def _approval_is_auto(fp: dict[str, Any]) -> bool:
    """True iff the fingerprint's approval aria is Automatically approve."""
    return bool(_AUTO_ARIA_RE.search(_approval_aria(fp)))


def cowork_auto_refuse_reason(fp: dict[str, Any]) -> str | None:
    """Fail-closed refuse text when a Cowork dispatch must not Start task.

    Callers (``send_prompt``) raise this string so Manual/Skip cannot ship.
    Chat (no approval chrome) and Project-shell rows without chips return
    None — named skip, not a silent Auto waiver on ``/new`` or ``/cowork/cse_``.
    """
    aria = _approval_aria(fp)
    mode = fp.get("mode")
    if mode == "chat" and not aria:
        return None
    if not aria and mode != "cowork":
        return None
    if _AUTO_ARIA_RE.search(aria):
        return None
    if _MANUAL_ARIA_RE.search(aria):
        return (
            "cowork dispatch refused: approval aria "
            f"{aria!r} (need Automatically approve)"
        )
    if _SKIP_ARIA_RE.search(aria):
        return (
            "cowork dispatch refused: approval aria "
            f"{aria!r} (need Automatically approve; Skip all is not Auto)"
        )
    return (
        "cowork dispatch refused: Automatically approve not attested "
        f"(mode={mode!r} approval={fp.get('approval')!r})"
    )


def _compose_attested(
    fp: dict[str, Any],
    mode: ComposeMode,
    *,
    require_auto: bool = True,
) -> bool:
    """Cowork ship-attest. ``require_auto=False`` is chip/title only (Manual ok)."""
    if mode == "cowork":
        if require_auto:
            return fp.get("mode") == "cowork" and _approval_is_auto(fp)
        return fp.get("mode") == "cowork" or bool(fp.get("approval"))
    return fp.get("mode") == "chat" and not fp.get("approval")


async def await_compose_attest(
    page,
    mode: ComposeMode,
    *,
    timeout_s: float = 8.0,
    poll_ms: int = _POLL_MS,
    require_auto: bool = True,
) -> dict[str, Any]:
    """Poll ``compose_mode_fingerprint`` until mode attests or timeout.

    Default ``require_auto=True`` is the Start-task ship gate: Cowork must
    show Automatically approve. Chip toggle (``select_compose_mode``) passes
    ``require_auto=False`` so Cowork+Manual can attest mode before Auto flip.
    """
    elapsed = 0.0
    last = await compose_mode_fingerprint(page)
    if _compose_attested(last, mode, require_auto=require_auto):
        return {
            "ok": True,
            "step": f"attested_{mode}",
            "fingerprint": last,
            "elapsed_ms": 0,
        }

    limit_ms = int(timeout_s * 1000)
    while elapsed < limit_ms:
        await page.wait_for_timeout(poll_ms)
        elapsed += poll_ms
        last = await compose_mode_fingerprint(page)
        if _compose_attested(last, mode, require_auto=require_auto):
            return {
                "ok": True,
                "step": f"attested_{mode}",
                "fingerprint": last,
                "elapsed_ms": elapsed,
            }
    return {
        "ok": False,
        "step": f"attest_{mode}_timeout",
        "fingerprint": last,
        "elapsed_ms": elapsed,
        "timeout_s": timeout_s,
    }


async def await_submit_visible(
    page,
    mode: ComposeMode,
    *,
    timeout_s: float = 8.0,
    poll_ms: int = _POLL_MS,
) -> dict[str, Any]:
    """Poll until Start task (cowork) or Send message (chat) is visible + enabled."""
    role_re = _SUBMIT_ROLE[mode]
    aria_sub = _SUBMIT_ARIA[mode]
    elapsed = 0.0
    limit_ms = int(timeout_s * 1000)

    async def _found() -> dict[str, Any] | None:
        loc = page.get_by_role("button", name=role_re)
        if await loc.count():
            btn = loc.first
            if await btn.is_visible() and not await btn.is_disabled():
                return {"via": "role", "name": role_re.pattern}
        loc = page.locator(f"button[aria-label*='{aria_sub}' i]")
        if await loc.count():
            btn = loc.first
            if await btn.is_visible() and not await btn.is_disabled():
                return {"via": "aria", "name": aria_sub}
        return None

    hit = await _found()
    if hit:
        return {"ok": True, "step": f"submit_visible_{mode}", **hit, "elapsed_ms": 0}

    while elapsed < limit_ms:
        await page.wait_for_timeout(poll_ms)
        elapsed += poll_ms
        hit = await _found()
        if hit:
            return {
                "ok": True,
                "step": f"submit_visible_{mode}",
                **hit,
                "elapsed_ms": elapsed,
            }
    return {
        "ok": False,
        "step": f"submit_{mode}_timeout",
        "wanted": "Start task" if mode == "cowork" else "Send message",
        "elapsed_ms": elapsed,
        "timeout_s": timeout_s,
    }


def resolve_submit_strategy(
    url: str, fp: dict[str, Any] | None = None
) -> SubmitStrategy:
    """Return ``live_discover`` on warm chat/CSE URLs; ``mode_locked`` on bare ``/new``."""
    _ = fp  # reserved — URL is the lifecycle signal (tier1-anchors / A bind)
    if in_active_chat(url or ""):
        return "live_discover"
    return "mode_locked"


def warm_submit_settle_ms() -> int:
    """Documented AM-4 post-refocus settle before live submit discovery."""
    return _WARM_SUBMIT_SETTLE_MS


def is_excluded_submit_control(
    *, aria: str = "", text: str = "", name: str = ""
) -> bool:
    """True when control matches DOM-sample exclude list (model/approval/stop/etc.)."""
    blob = " ".join(p for p in (aria, text, name) if p).strip()
    if not blob:
        return True
    return bool(_EXCLUDE_SUBMIT_RE.search(blob))


def is_positive_submit_match(*, aria: str = "", text: str = "", name: str = "") -> bool:
    """True for warm follow-up submit labels (DOM sample: ``Send`` not ``Send message``)."""
    for part in (aria, text, name):
        part = (part or "").strip()
        if part and _POSITIVE_SUBMIT_RE.match(part):
            return True
    return False


def pick_submit_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose one composer-local submit from collected DOM candidates."""
    eligible: list[dict[str, Any]] = []
    positive: list[dict[str, Any]] = []
    for raw in candidates:
        aria = str(raw.get("aria") or "")
        text = str(raw.get("text") or "")
        name = str(raw.get("name") or aria or text)
        if is_excluded_submit_control(aria=aria, text=text, name=name):
            continue
        row = {**raw, "aria": aria, "text": text, "name": name}
        eligible.append(row)
        if is_positive_submit_match(aria=aria, text=text, name=name):
            positive.append(row)
    if positive:
        positive.sort(key=lambda c: (len(c.get("name") or ""), c.get("name") or ""))
        hit = positive[0]
        return {**hit, "pick": "positive_match"}
    if len(eligible) == 1:
        return {**eligible[0], "pick": "unique_candidate"}
    return None


_COLLECT_COMPOSER_SUBMIT_JS = """() => {
  const composer = document.querySelector('[data-testid="chat-input"]')
    || document.querySelector('[contenteditable="true"][data-testid]')
    || document.querySelector('[contenteditable="true"]');
  if (!composer) {
    return { ok: false, step: 'composer_missing', candidates: [] };
  }
  const rect = composer.getBoundingClientRect();
  const pad = 140;
  const region = {
    left: rect.left - 24,
    top: rect.top - 24,
    right: rect.right + pad,
    bottom: rect.bottom + pad,
  };
  const inRegion = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    return cx >= region.left && cx <= region.right
      && cy >= region.top && cy <= region.bottom;
  };
  const candidates = [];
  for (const btn of document.querySelectorAll('button, [role="button"]')) {
    if (btn.disabled || btn.getAttribute('aria-disabled') === 'true') continue;
    const style = window.getComputedStyle(btn);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    if (parseFloat(style.opacity || '1') === 0) continue;
    if (!inRegion(btn)) continue;
    const aria = (btn.getAttribute('aria-label') || '').trim();
    const text = (btn.innerText || '').trim().replace(/\\s+/g, ' ');
    const name = aria || text;
    if (!name) continue;
    candidates.push({
      aria,
      text,
      name,
      role: btn.getAttribute('role') || 'button',
    });
  }
  return { ok: true, step: 'collected', candidates };
}"""


async def discover_live_submit(page, *, composer=None) -> dict[str, Any]:
    """Find enabled submit control composer-local on warm sessions (friction 25291)."""
    collected = await page.evaluate(_COLLECT_COMPOSER_SUBMIT_JS)
    if not collected.get("ok"):
        return {
            "ok": False,
            "step": collected.get("step") or "composer_missing",
            "candidates": [],
        }
    picked = pick_submit_candidate(list(collected.get("candidates") or []))
    if picked is None:
        return {
            "ok": False,
            "step": "no_submit_candidate",
            "candidates": collected.get("candidates") or [],
        }
    return {
        "ok": True,
        "step": "discovered",
        "via": "composer_local",
        "name": picked.get("name"),
        "aria": picked.get("aria"),
        "text": picked.get("text"),
        "pick": picked.get("pick"),
        "strategy": "live_discover",
    }


async def await_live_submit_visible(
    page,
    *,
    composer=None,
    timeout_s: float = 8.0,
    poll_ms: int = _POLL_MS,
) -> dict[str, Any]:
    """Poll ``discover_live_submit`` until a composer-local submit is found."""
    elapsed = 0.0
    limit_ms = int(timeout_s * 1000)
    hit = await discover_live_submit(page, composer=composer)
    if hit.get("ok"):
        return {**hit, "elapsed_ms": 0}
    last = hit
    while elapsed < limit_ms:
        await page.wait_for_timeout(poll_ms)
        elapsed += poll_ms
        hit = await discover_live_submit(page, composer=composer)
        if hit.get("ok"):
            return {**hit, "elapsed_ms": elapsed}
        last = hit
    return {
        "ok": False,
        "step": "live_discover_timeout",
        "elapsed_ms": elapsed,
        "timeout_s": timeout_s,
        "last": last,
    }


async def click_discovered_submit(
    page, discovery: dict[str, Any], *, composer=None
) -> None:
    """Click a submit control returned by ``discover_live_submit`` (composer-local)."""
    from claude_bundles.composer_submit import click_submit_button

    name = str(discovery.get("name") or discovery.get("aria") or "")
    if not name:
        raise RuntimeError("discovered submit missing name/aria")
    aria = str(discovery.get("aria") or "")
    if aria:
        loc = page.locator(f"button[aria-label='{aria}']")
        if await loc.count() and await click_submit_button(loc.first):
            return
    loc = page.get_by_role("button", name=re.compile(re.escape(name), re.I))
    if await loc.count() and await click_submit_button(loc.first):
        return
    raise RuntimeError(f"discovered submit not clickable: {name!r}")


async def probe_compose_toggle_timeline(
    page,
    mode: ComposeMode,
    *,
    click_fn,
    sample_ms: tuple[int, ...] = (0, 500, 1500, 3000),
) -> dict[str, Any]:
    """Phase-0 Jupiter probe: fingerprint samples after compose chip click."""
    before = await compose_mode_fingerprint(page)
    click_result = await click_fn(page, mode)
    timeline: list[dict[str, Any]] = []
    prev_ms = 0
    for ms in sample_ms:
        if ms > prev_ms:
            await page.wait_for_timeout(ms - prev_ms)
        fp = await compose_mode_fingerprint(page)
        timeline.append(
            {
                "t_ms": ms,
                "fingerprint": fp,
                "attested": _compose_attested(fp, mode, require_auto=False),
            }
        )
        prev_ms = ms
    return {
        "mode": mode,
        "before": before,
        "click": click_result,
        "timeline": timeline,
        "class": "dual_primary",
    }
