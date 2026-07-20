"""Thin CDP sealed-ask against a Cowork Project (disposable subagent).

Hot path (agent-bus:4917 / 5129 pivot):
  sealed prompt → fresh Project chat → wait → scrape → optional delete
  → caller writes cortex sidecar + continuity (bus optional).

Uses the authenticated ``claude-ai-chrome-profile`` on Jupiter CDP.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from playwright.async_api import Page

from claude_bundles.chat_model_match import sealed_ask_default_effort
from claude_bundles.chat_model_select import (
    label_satisfies_request,
    parse_model_request,
    select_model,
)
from claude_bundles.chat_reply_wait import harvest_assistant, wait_assistant_reply
from claude_bundles.chat_session_hygiene import (
    delete_chat_if_active,
    goto_fresh_compose,
    pick_chat_page,
)
from claude_bundles.compose_attest import (
    await_compose_attest,
    await_live_submit_visible,
    await_submit_visible,
    click_discovered_submit,
    compose_mode_fingerprint,
    resolve_submit_strategy,
    warm_submit_settle_ms,
)
from claude_bundles.project_chrome import project_url
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL, connect_cdp

_THINKING_LINE = re.compile(
    r"^(Thinking about .+|Thinking\b.*)$",
    re.I | re.M,
)
_EXECUTION_ID_LINE = re.compile(r"^- execution_id: `([^`]+)`", re.M)


def strip_thinking_prefix(body: str) -> str:
    """Drop extended-thinking chrome lines that pollute harvest (dogfood 4917)."""
    text = (body or "").strip()
    if not text:
        return ""
    cleaned = _THINKING_LINE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


@dataclass(frozen=True)
class ProjectAskResult:
    ok: bool
    body: str
    url: str
    project_uuid: str
    project_url: str
    model: dict[str, Any]
    body_len: int
    delete_after: dict[str, Any] | None
    error: str | None = None
    archive_uri: str | None = None
    attested_model: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _attest_model(requested: str, state: dict[str, Any], selected: dict[str, Any]) -> str | None:
    """Return attested label or None if mismatch against requested model.

    Uses the same UI-pattern matcher as ``select_model`` (friction a24692) —
    not a hardcoded family allowlist.
    """
    label = (state.get("model_label") or selected.get("current_model") or "").strip()
    if not label:
        return None
    req = requested or ""
    family, effort = parse_model_request(req)
    if effort is None:
        effort = sealed_ask_default_effort(family)
    if not label_satisfies_request(req, label, effort=effort):
        raise RuntimeError(
            f"model attestation mismatch: wanted {req!r}, got {label!r}"
        )
    return label


def read_archive_execution_id(archive_path: str) -> str | None:
    """Return execution_id stamped in an existing harvest file, if any."""
    from pathlib import Path

    path = Path(archive_path)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _EXECUTION_ID_LINE.search(text)
    return match.group(1) if match else None


def archive_harvest(
    *,
    body: str,
    url: str,
    project_uuid: str,
    model: dict[str, Any],
    attested_model: str | None,
    archive_path: str,
    execution_id: str | None = None,
) -> str:
    """Persist raw harvest before delete. Returns cortex:// or file URI."""
    from datetime import UTC, datetime
    from pathlib import Path

    path = Path(archive_path)
    if path.is_file() and execution_id:
        existing = read_archive_execution_id(archive_path)
        if existing and existing != execution_id:
            raise RuntimeError(
                "archive path occupied by foreign execution "
                f"(existing={existing[:8]}, requested={execution_id[:8]}): "
                f"{archive_path}"
            )
        if existing is None:
            raise RuntimeError(
                f"archive path occupied without execution_id metadata: {archive_path}"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat()
    exec_line = (
        f"- execution_id: `{execution_id}`\n" if execution_id else ""
    )
    text = (
        f"# CDP ask harvest\n\n"
        f"- archived_at: `{stamp}`\n"
        f"{exec_line}"
        f"- url: `{url}`\n"
        f"- project_uuid: `{project_uuid}`\n"
        f"- model_select: `{model}`\n"
        f"- attested_model: `{attested_model}`\n\n"
        f"## Body\n\n{body}\n"
    )
    path.write_text(text, encoding="utf-8")
    resolved = str(path.resolve())
    if "/mcp-data/files/" in resolved:
        rel = resolved.split("/mcp-data/files/", 1)[1]
        return f"cortex://{rel}"
    return f"file://{resolved}"


async def find_composer(page: Page):
    """Prefer the Project chat input observed 2026-07-16."""
    preferred = page.locator('[data-testid="chat-input"]')
    if await preferred.count():
        for i in range(await preferred.count()):
            el = preferred.nth(i)
            if await el.is_visible():
                return el
    for sel in (
        "[contenteditable='true'][data-testid]",
        "[contenteditable='true']",
        "textarea",
        "[role='textbox']",
    ):
        loc = page.locator(sel)
        for i in range(await loc.count()):
            el = loc.nth(i)
            if await el.is_visible():
                return el
    return None


# Cowork /new submit control is "Start task"; Chat is "Send message" (24609).
_SUBMIT_ROLE_RES = (
    re.compile(r"start task", re.I),
    re.compile(r"send message", re.I),
)
_SUBMIT_ARIA_SUBSTRS = ("Start task", "Send")


def submit_control_names() -> tuple[str, ...]:
    """Ordered submit aria/role names for Cowork-first then Chat."""
    return ("Start task", "Send message")


_LEADING_SLASH_SKILL = re.compile(r"^(/[\w-]+)(?:\r?\n)+(.*)\Z", re.DOTALL)


async def _insert_prompt_text(page: Page, text: str) -> None:
    """Fill the composer, binding a leading ``/skill`` before the body.

    Claude.ai slash skills need the token typed and confirmed with a newline
    (menu select / chip bind) before the rest of the sealed prompt. A single
    ``insert_text`` of ``/prose-discipline\\n\\nROLE…`` skips that bind.
    """
    m = _LEADING_SLASH_SKILL.match(text)
    if m is None:
        await page.keyboard.insert_text(text)
        return
    slash, rest = m.group(1), m.group(2)
    # Character typing surfaces the slash menu; paste does not.
    await page.keyboard.type(slash, delay=25)
    await page.wait_for_timeout(450)
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(350)
    if rest:
        await page.keyboard.insert_text(rest)


async def send_prompt(page: Page, text: str) -> None:
    composer = await find_composer(page)
    if composer is None:
        raise RuntimeError("composer not found on page")
    await composer.click(force=True)
    await page.wait_for_timeout(300)
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await _insert_prompt_text(page, text)
    await page.wait_for_timeout(600)

    fp = await compose_mode_fingerprint(page)
    strategy = resolve_submit_strategy(page.url or "", fp)

    if strategy == "live_discover":
        await composer.click(force=True)
        await page.wait_for_timeout(warm_submit_settle_ms())
        submit = await await_live_submit_visible(page, composer=composer, timeout_s=8.0)
        if not submit.get("ok"):
            raise RuntimeError(
                "submit control missing on warm session: need composer-local enabled "
                f"submit (live_discover) — refusing Enter fallback; last={submit.get('last')}"
            )
        await click_discovered_submit(page, submit, composer=composer)
        return

    mode: str = fp.get("mode") or ("cowork" if fp.get("approval") else "chat")
    if mode not in ("chat", "cowork"):
        mode = "cowork" if fp.get("approval") else "chat"
    attest = await await_compose_attest(page, mode, timeout_s=4.0)  # type: ignore[arg-type]
    if not attest.get("ok"):
        raise RuntimeError(
            f"compose re-attest failed before send: mode={mode!r} "
            f"fingerprint={attest.get('fingerprint')}"
        )

    submit = await await_submit_visible(page, mode, timeout_s=8.0)
    if submit.get("ok"):
        role_re = _SUBMIT_ROLE_RES[0 if mode == "cowork" else 1]
        loc = page.get_by_role("button", name=role_re)
        if await loc.count():
            btn = loc.first
            if await btn.is_visible() and not await btn.is_disabled():
                await btn.click(force=True)
                return
        aria = _SUBMIT_ARIA_SUBSTRS[0 if mode == "cowork" else 1]
        loc = page.locator(f"button[aria-label*='{aria}' i]")
        if await loc.count():
            btn = loc.first
            if await btn.is_visible() and not await btn.is_disabled():
                await btn.click(force=True)
                return

    raise RuntimeError(
        "submit control missing: need visible Start task (Cowork) or "
        "Send message (Chat) — refusing Enter fallback"
    )


async def project_ask_on_page(
    page: Page,
    prompt: str,
    *,
    project_uuid: str,
    model: str = "opus-4.8",
    delete_after: bool = True,
    timeout_s: int = 360,
    min_growth: int = 50,
    min_body: int = 40,
    archive_path: str | None = None,
    execution_id: str | None = None,
) -> ProjectAskResult:
    """Run one sealed ask on an existing Playwright page."""
    dest = project_url(project_uuid)
    try:
        await goto_fresh_compose(page, project_uuid=project_uuid)
        # Cowork Project: model picker mounts after composer chrome is live.
        composer = await find_composer(page)
        if composer is not None:
            await composer.click(force=True)
            await page.wait_for_timeout(800)
        model_info = await select_model(page, model)
        if not model_info.get("ok"):
            return ProjectAskResult(
                ok=False,
                body="",
                url=page.url,
                project_uuid=project_uuid,
                project_url=dest,
                model=model_info,
                body_len=0,
                delete_after=None,
                error=f"model select failed: {model_info}",
            )
        before = await harvest_assistant(page)
        await send_prompt(page, prompt)
        state = await wait_assistant_reply(
            page,
            before=before,
            timeout_s=timeout_s,
            poll_ms=500,
            min_growth=min_growth,
            min_body=min_body,
        )
        body = strip_thinking_prefix(state.get("body") or "")
        attested = _attest_model(model, state, model_info)
        archive_uri = None
        if archive_path:
            archive_uri = archive_harvest(
                body=body,
                url=str(state.get("url") or page.url),
                project_uuid=project_uuid,
                model=model_info,
                attested_model=attested,
                archive_path=archive_path,
                execution_id=execution_id,
            )
        elif delete_after:
            # Fable MUST: delete only after archive — refuse if no path provided
            return ProjectAskResult(
                ok=False,
                body=body,
                url=str(state.get("url") or page.url),
                project_uuid=project_uuid,
                project_url=dest,
                model=model_info,
                body_len=len(body),
                delete_after=None,
                error="archive_path required before delete (archive-before-delete bind)",
                attested_model=attested,
            )
        delete_result = None
        if delete_after and archive_uri:
            delete_result = await delete_chat_if_active(page, return_to=dest)
        return ProjectAskResult(
            ok=True,
            body=body,
            url=str(state.get("url") or page.url),
            project_uuid=project_uuid,
            project_url=dest,
            model=model_info,
            body_len=len(body),
            delete_after=delete_result,
            archive_uri=archive_uri,
            attested_model=attested,
        )
    except Exception as exc:  # noqa: BLE001 — surface to CLI ledger; ¬delete
        return ProjectAskResult(
            ok=False,
            body="",
            url=page.url or "",
            project_uuid=project_uuid,
            project_url=dest,
            model={},
            body_len=0,
            delete_after=None,
            error=str(exc),
        )


async def run_project_ask(
    prompt: str,
    *,
    project_uuid: str,
    model: str = "opus-4.8",
    delete_after: bool = True,
    cdp_url: str = DEFAULT_CDP_URL,
    timeout_s: int = 360,
    min_growth: int = 50,
    min_body: int = 40,
    archive_path: str | None = None,
    execution_id: str | None = None,
) -> ProjectAskResult:
    """Connect CDP, run one sealed ask, disconnect."""
    pw, _browser, ctx, _page0 = await connect_cdp(cdp_url)
    try:
        page = await pick_chat_page(ctx)
        return await project_ask_on_page(
            page,
            prompt,
            project_uuid=project_uuid,
            model=model,
            delete_after=delete_after,
            timeout_s=timeout_s,
            min_growth=min_growth,
            min_body=min_body,
            archive_path=archive_path,
            execution_id=execution_id,
        )
    finally:
        await pw.stop()
