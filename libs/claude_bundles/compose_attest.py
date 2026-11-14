"""Shared compose attestation — mode fingerprint, poll-until-attest, submit wait.

Dual-primary (friction 25051/25052): selector repair + poll-until-attest, not
poll-only. Chip toggle may attest Cowork while approval is still Manual.
Ship gate (Start task / warm Send): compose v2 requires Permission mode
``Auto`` (aria-less chip text ``Auto`` counts). Legacy Cowork chrome may
still show ``Skip all approvals`` or ``Skip`` — both pass the gate.
Chat success: ``mode==chat`` and approval is None. Project-shell rows without
chips are a named skip, not a silent Skip waiver on ``/new``.

a:31319 (2026-08-30): Anthropic dropped the descriptive aria-label on the
approval chip — a live census against a fresh ``/new`` tab found a bare
``<button>`` with ``aria-label=""`` and plain ASCII text ``"Auto"`` (no
private-use-area prefix; that theory is falsified). ``compose_mode_fingerprint``
now falls back to the short text label when aria is absent.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from claude_bundles.chat_session_hygiene import in_active_chat
from claude_bundles.compose_chip_probe import compose_readiness_attestation
from claude_bundles.composer_locate import (
    COLLECT_COMPOSER_SUBMIT_JS as _COLLECT_COMPOSER_SUBMIT_JS,
)

ApprovalMode = Literal["skip", "auto", "manual", "unknown", "absent"]
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
# Short-form alternation (a:31319) — the chip can render with no aria-label at
# all, just a bare "Auto"/"Manual"/"Skip" button label.
_AUTO_ARIA_RE = re.compile(
    r"Automatically approve|^Auto$|Permission mode:\s*Auto\b",
    re.I,
)
_MANUAL_ARIA_RE = re.compile(
    r"Manually approve|^Manual$|Permission mode:\s*Manual\b",
    re.I,
)
_SKIP_ARIA_RE = re.compile(
    r"Skip all approvals|Never pause|^Skip$|Permission mode:\s*Skip\b",
    re.I,
)
_SUBMIT_ROLE = {
    "cowork": re.compile(r"start task", re.I),
    "chat": re.compile(r"send message", re.I),
}
_SUBMIT_ARIA = {
    "cowork": "Start task",
    "chat": "Send",
}


async def compose_mode_fingerprint(page) -> dict[str, Any]:
    """Lightweight attest: title + approval control currently shown.

    Prefers the descriptive aria-label (``/approve/i``). Falls back to a bare
    short text label (``"Auto"``/``"Manual"``/``"Skip"``) when no button
    carries that aria at all — a:31319, confirmed via live census: the chip
    can render aria-label-less with the raw state name as its only signal.
    """
    title = await page.title()
    approval = await page.evaluate(
        """() => {
          const els = Array.from(document.querySelectorAll('button, [role="button"]'));
          for (const el of els) {
            const aria = el.getAttribute('aria-label') || '';
            if (/approve/i.test(aria)) {
              return {
                aria,
                text: (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40),
                via: 'aria',
              };
            }
          }
          function labelledByText(el) {
            const ids = (el.getAttribute('aria-labelledby') || '').trim().split(/\\s+/);
            return ids.filter(Boolean).map((id) => {
              const n = document.getElementById(id);
              return n ? (n.innerText || n.textContent || '').trim() : '';
            }).filter(Boolean).join(' ');
          }
          for (const el of els) {
            const text = (el.innerText || '').trim().replace(/\\s+/g, ' ');
            if (text.length > 0 && text.length <= 12 && /^(auto|manual|skip)/i.test(text)) {
              const labelledby = labelledByText(el);
              const out = { aria: '', text, via: 'text' };
              if (labelledby) out.labelledby = labelledby.slice(0, 120);
              return out;
            }
          }
          return null;
        }"""
    )
    mode: str | None = None
    title_fp: str | None = None
    if re.search(r"new task", title, re.I):
        mode = "cowork"
        title_fp = "new_task"
    elif re.search(r"new chat", title, re.I):
        mode = "chat"
        title_fp = "new_chat"
    elif re.search(r"new session", title, re.I):
        mode = None
        title_fp = "new_session"
    out: dict[str, Any] = {
        "title": title,
        "mode": mode,
        "approval": approval,
        "url": page.url,
    }
    if title_fp is not None:
        out["title_fingerprint"] = title_fp
    readiness = await compose_readiness_attestation(page)
    if readiness.get("ready"):
        out["compose_readiness"] = readiness
        if out.get("title_fingerprint") == "new_session" and out.get("mode") is None:
            out["mode"] = "chat"
    return out


def approval_label(fp: dict[str, Any]) -> str:
    """Return the approval control's aria-label, or its short text label when
    aria is absent (a:31319). Shared with ``chat_cowork_mode.set_approval_mode``
    so both modules classify the aria-less short-label chip identically.
    """
    approval = fp.get("approval")
    if not isinstance(approval, dict):
        return ""
    aria = str(approval.get("aria") or "")
    if aria:
        return aria
    labelledby = str(approval.get("labelledby") or "")
    if labelledby:
        return labelledby
    return str(approval.get("text") or "")


def _approval_aria(fp: dict[str, Any]) -> str:
    """Return the approval chip's label, or empty when chrome is absent."""
    return approval_label(fp)


def _approval_is_auto(fp: dict[str, Any]) -> bool:
    """True iff the fingerprint's approval aria is Automatically approve."""
    return bool(_AUTO_ARIA_RE.search(_approval_aria(fp)))


def _approval_is_skip(fp: dict[str, Any]) -> bool:
    """True iff the label is Skip all approvals or the short text Skip."""
    return bool(_SKIP_ARIA_RE.search(_approval_aria(fp)))


def approval_mode_chip(fp: dict[str, Any]) -> ApprovalMode:
    """Classify the approval chip for pass receipts (skip | auto | manual | unknown | absent)."""
    aria = _approval_aria(fp)
    if not aria:
        return "absent"
    if _SKIP_ARIA_RE.search(aria):
        return "skip"
    if _MANUAL_ARIA_RE.search(aria):
        return "manual"
    if _AUTO_ARIA_RE.search(aria):
        return "auto"
    return "unknown"


def dispatch_approval_refuse_reason(
    fp: dict[str, Any],
    *,
    surface: str | None = None,
) -> str | None:
    """Surface-aware approval gate before Start task / Send.

    ``surface=None`` preserves legacy Cowork/Chat behavior via
    ``cowork_auto_refuse_reason``. ``surface='code'`` accepts Skip or Auto;
    Manual is refused. Callers on ``/code`` must not flip the chip to Skip.
    """
    surf = (surface or "").strip().lower() or None
    if surf != "code":
        return cowork_auto_refuse_reason(fp)

    aria = _approval_aria(fp)
    mode = fp.get("mode")
    if mode == "chat" and not aria:
        return None
    if not aria and mode != "cowork":
        return None
    chip = approval_mode_chip(fp)
    if chip in ("skip", "auto"):
        return None
    if chip == "manual":
        seen = str(fp.get("approval", {}).get("text") or aria).strip() or aria
        return (
            f"code dispatch refused: surface=code approval {seen!r} "
            "(code accepts skip, auto)"
        )
    return cowork_auto_refuse_reason(fp)


def cowork_auto_refuse_reason(fp: dict[str, Any]) -> str | None:
    """Fail-closed refuse text when a Cowork dispatch must not Start task.

    Callers (``send_prompt``) raise this string so Manual cannot ship.
    None iff the chip reads Auto (v2 Permission mode) or legacy Skip-all.
    Chat (no approval chrome) and Project-shell rows without chips return
    None — named skip, not a silent waiver on ``/new`` or ``/cowork/cse_``.
    """
    aria = _approval_aria(fp)
    mode = fp.get("mode")
    if mode == "chat" and not aria:
        return None
    if not aria and mode != "cowork" and not fp.get("approval"):
        return None
    seen = str(fp.get("approval", {}).get("text") or aria).strip() or aria
    if _AUTO_ARIA_RE.search(aria):
        return None
    if _SKIP_ARIA_RE.search(aria):
        return None
    if _MANUAL_ARIA_RE.search(aria):
        return (
            f"cowork dispatch refused: approval {seen!r} "
            "(need Permission mode Skip or Auto)"
        )
    return (
        "cowork dispatch refused: Permission mode Skip or Auto not attested "
        f"(mode={mode!r} approval={fp.get('approval')!r})"
    )


def _compose_attested(
    fp: dict[str, Any],
    mode: ComposeMode,
    *,
    require_auto: bool = True,
) -> bool:
    """Cowork ship-attest. ``require_auto=True`` requires Permission Auto (v2)
    or legacy Skip-all.

    ``require_auto=False`` is chip/title only so the Cowork chip can attest
    before the radio flip (Manual or Auto ok at that step).
    """
    if mode == "cowork":
        if require_auto:
            if fp.get("approval"):
                return _approval_is_auto(fp) or _approval_is_skip(fp)
            return fp.get("mode") == "cowork"
        return fp.get("mode") == "cowork" or bool(fp.get("approval"))
    if fp.get("mode") == "chat" and not fp.get("approval"):
        return True
    readiness = fp.get("compose_readiness")
    if isinstance(readiness, dict) and readiness.get("ready"):
        return True
    return False


async def await_compose_attest(
    page,
    mode: ComposeMode,
    *,
    timeout_s: float = 8.0,
    poll_ms: int = _POLL_MS,
    require_auto: bool = True,
) -> dict[str, Any]:
    """Poll ``compose_mode_fingerprint`` until mode attests or timeout.

    Default ``require_auto=True`` is the Start-task ship gate: Permission
    mode Auto or legacy Skip-all (aria-less ``Skip`` counts). Chip toggle
    (``select_compose_mode``) passes ``require_auto=False`` so the Cowork
    chip can attest before the radio flip.
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
    url: str,
    fp: dict[str, Any] | None = None,
    *,
    surface: str | None = None,
) -> SubmitStrategy:
    """Pick submit path from URL lifecycle and compose surface.

    Warm chat/CSE URLs and ``surface='code'`` use live DOM submit discovery;
    bare ``/new`` Cowork first-send keeps mode-locked re-attest + Start task.
    """
    _ = fp  # reserved — URL is the lifecycle signal (tier1-anchors / A bind)
    surf = (surface or "").strip().lower() or None
    if surf == "code":
        return "live_discover"
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
