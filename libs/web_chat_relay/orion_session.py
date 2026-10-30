"""Probe and optional sign-in for chatgpt.com on io Orion CDP (:9342).

Credentials come from ``~/.gateway/secrets/chatgpt.env`` only — never argv or chat.
Email 2FA is read from the mailbox when the login page asks for a code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_bundles.skills_ui_panel import connect_cdp

DEFAULT_CDP_URL = "http://127.0.0.1:9342"
_SECRETS = Path.home() / ".gateway" / "secrets" / "chatgpt.env"
_LOGIN_HINT = re.compile(
    r"log in|sign in|create an account|welcome back|continue with google",
    re.I,
)

PROBE_JS = """
() => {
  const body = (document.body && document.body.innerText) || "";
  const url = location.href;
  const composer = document.querySelector("#prompt-textarea, textarea[data-id], textarea");
  const loginWall = /log in|sign in to continue|welcome back/i.test(body)
    || /auth\\.openai\\.com/i.test(url);
  return {
    url,
    title: document.title,
    login_wall: loginWall,
    signed_in: !loginWall && !!composer && /chatgpt\\.com/i.test(url),
    composer: !!composer,
    body_start: body.slice(0, 1200),
  };
}
"""


@dataclass(frozen=True)
class OrionProbe:
    url: str
    signed_in: bool
    login_wall: bool
    composer: bool
    title: str
    body_start: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "signed_in": self.signed_in,
            "login_wall": self.login_wall,
            "composer": self.composer,
            "title": self.title,
            "body_start": self.body_start,
        }


def load_secrets() -> dict[str, str]:
    if not _SECRETS.is_file():
        return {}
    out: dict[str, str] = {}
    for line in _SECRETS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip("'\"")
    return out


async def probe_orion(*, cdp_url: str = DEFAULT_CDP_URL) -> OrionProbe:
    _pw, _browser, _ctx, page = await connect_cdp(cdp_url)
    try:
        if "chatgpt.com" not in page.url:
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60_000)
        raw = await page.evaluate(PROBE_JS)
        return OrionProbe(
            url=str(raw.get("url") or page.url),
            signed_in=bool(raw.get("signed_in")),
            login_wall=bool(raw.get("login_wall")),
            composer=bool(raw.get("composer")),
            title=str(raw.get("title") or ""),
            body_start=str(raw.get("body_start") or ""),
        )
    finally:
        await _browser.close()
        await _pw.stop()


async def login_orion(*, cdp_url: str = DEFAULT_CDP_URL) -> dict[str, Any]:
    secrets = load_secrets()
    email = secrets.get("CHATGPT_USERNAME", "").strip()
    password = secrets.get("CHATGPT_PASSWORD", "").strip()
    if not email or not password:
        return {
            "ok": False,
            "error": "missing_secrets",
            "hint": "Set CHATGPT_USERNAME and CHATGPT_PASSWORD in ~/.gateway/secrets/chatgpt.env",
        }

    _pw, _browser, _ctx, page = await connect_cdp(cdp_url)
    try:
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60_000)
        before = await page.evaluate(PROBE_JS)
        if before.get("signed_in"):
            return {"ok": True, "already_signed_in": True, "url": page.url}

        login_btn = page.get_by_role("button", name=re.compile(r"log in|sign in", re.I))
        if await login_btn.count():
            await login_btn.first.click(timeout=15_000)

        email_input = page.locator(
            'input[type="email"], input[name="email"], input[autocomplete="email"]'
        )
        await email_input.first.wait_for(state="visible", timeout=20_000)
        await email_input.first.fill(email)
        continue_btn = page.get_by_role("button", name=re.compile(r"continue", re.I))
        if await continue_btn.count():
            await continue_btn.first.click(timeout=15_000)

        pass_input = page.locator('input[type="password"]')
        await pass_input.first.wait_for(state="visible", timeout=20_000)
        await pass_input.first.fill(password)
        submit = page.get_by_role("button", name=re.compile(r"continue|log in", re.I))
        if await submit.count():
            await submit.first.click(timeout=15_000)

        await page.wait_for_timeout(4000)
        body = (await page.evaluate("() => document.body.innerText")) or ""
        url = page.url
        if "auth.openai.com" in url and _LOGIN_HINT.search(body):
            return {
                "ok": False,
                "error": "email_code_required",
                "url": url,
                "hint": "Email 2FA. Read the code from the mailbox, then submit it on this page.",
            }
        after = await page.evaluate(PROBE_JS)
        return {
            "ok": bool(after.get("signed_in")),
            "url": after.get("url") or url,
            "signed_in": bool(after.get("signed_in")),
            "login_wall": bool(after.get("login_wall")),
        }
    finally:
        await _browser.close()
        await _pw.stop()
