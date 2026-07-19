"""Shared compose attestation — mode fingerprint, poll-until-attest, submit wait.

Dual-primary (friction 25051/25052): selector repair + poll-until-attest, not
poll-only. Cowork success: ``mode==cowork`` or approval present. Chat success:
``mode==chat`` and approval is None.
"""

from __future__ import annotations

import re
from typing import Any, Literal

ComposeMode = Literal["chat", "cowork"]

_POLL_MS = 400
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


def _compose_attested(fp: dict[str, Any], mode: ComposeMode) -> bool:
    if mode == "cowork":
        return fp.get("mode") == "cowork" or bool(fp.get("approval"))
    return fp.get("mode") == "chat" and not fp.get("approval")


async def await_compose_attest(
    page,
    mode: ComposeMode,
    *,
    timeout_s: float = 8.0,
    poll_ms: int = _POLL_MS,
) -> dict[str, Any]:
    """Poll ``compose_mode_fingerprint`` until mode attests or timeout."""
    elapsed = 0.0
    last = await compose_mode_fingerprint(page)
    if _compose_attested(last, mode):
        return {"ok": True, "step": f"attested_{mode}", "fingerprint": last, "elapsed_ms": 0}

    limit_ms = int(timeout_s * 1000)
    while elapsed < limit_ms:
        await page.wait_for_timeout(poll_ms)
        elapsed += poll_ms
        last = await compose_mode_fingerprint(page)
        if _compose_attested(last, mode):
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
                "attested": _compose_attested(fp, mode),
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
