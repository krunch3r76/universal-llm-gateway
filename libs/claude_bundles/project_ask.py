"""Thin CDP sealed-ask against a Cowork Project (disposable subagent).

Hot path (agent-bus:4917 / 5129 pivot):
  sealed prompt → fresh Project chat → wait → scrape → optional delete
  → caller writes cortex sidecar + continuity (bus optional).

Uses the authenticated ``claude-ai-chrome-profile`` on Jupiter CDP.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from claude_bundles.chat_model_match import (
    normalize_picker_request,
    sealed_ask_default_effort,
)
from claude_bundles.chat_model_select import (
    label_satisfies_request,
    parse_model_request,
    picker_attests_request,
    select_model,
)
from claude_bundles.chat_reply_wait import harvest_assistant, wait_assistant_reply
from claude_bundles.chat_session_hygiene import (
    delete_chat_if_active,
    goto_fresh_compose,
    in_active_chat,
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
from claude_bundles.cowork_output_download import (
    ExpectedSize,
    HarvestProvenance,
    HarvestSource,
    OutputDownloadError,
    cortex_files_root_from_env,
    resolve_harvest_body,
)
from claude_bundles.project_chrome import project_url
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL, connect_cdp

_THINKING_LINE = re.compile(
    r"^(Thinking about .+|Thinking\b.*)$",
    re.I | re.M,
)
_EXECUTION_ID_LINE = re.compile(r"^- execution_id: `([^`]+)`", re.M)


class HarvestArchiveError(RuntimeError):
    """Raised when ``archive_harvest`` refuses to overwrite mismatched on-disk bytes.

    Mapped to ``ok=false`` terminal by callers — may leave correct rich bytes on
    disk without clobbering them with a thinner harvest body.
    """


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
    harvest_provenance: HarvestProvenance | None = None

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
    req = normalize_picker_request(requested or "")
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
    path = Path(archive_path)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _EXECUTION_ID_LINE.search(text)
    return match.group(1) if match else None


def _archive_body_section(full_text: str) -> str:
    marker = "## Body\n\n"
    idx = full_text.find(marker)
    if idx < 0:
        return ""
    return full_text[idx + len(marker) :]


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
    """Persist raw harvest before delete. Returns cortex:// or file URI.

    Raises:
        HarvestArchiveError: When *archive_path* already exists and its on-disk
            sha256 differs from the bytes about to be written (defense-in-depth
            against clobber). Callers map this to ``ok=false`` terminal.
        RuntimeError: When *archive_path* is occupied by a foreign execution.
    """
    from datetime import UTC, datetime

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
    if path.is_file():
        try:
            existing_text = path.read_text(encoding="utf-8")
        except OSError:
            existing_text = ""
        existing_body = _archive_body_section(existing_text)
        if existing_body:
            same_body = hashlib.sha256(existing_body.encode()).digest() == hashlib.sha256(
                body.encode()
            ).digest()
            same_exec = (
                execution_id is not None
                and read_archive_execution_id(archive_path) == execution_id
            )
            upgrade = same_exec and len(body) > len(existing_body)
            if not same_body and not upgrade:
                raise HarvestArchiveError(
                    "archive path sha256 mismatch — refusing overwrite: "
                    f"{archive_path}"
                )
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
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


async def _insert_prompt_text(page: Page, text: str, *, composer) -> None:
    """Fill the composer: attach Claude skills via + menu, then paste body.

    **Canonical skill attach (operator 2026-07-26):** ``+`` → **Skills** →
    pick each Customize skill from the list. Slash-type multi-chip is broken
    (friction a25806 — only the first ``/<slug>`` binds).

    Leading ``/<slug>\\n`` lines in the sealed prompt are the **skill manifest**
    for attach (``shared_sync`` / known Claude Customize slugs only); they are
    stripped and never typed. Non-Claude / ``cursor_only`` slugs must not
    appear as slash lines (``prepend_cdp_dispatch_skills`` / ``partition_cdp_skills``)
    — if they do, attach is skipped (inline ``<skills_inline>`` carries delivery).
    Hybrid ``Use the … skill`` / ``<skills_inline>`` ride in ``rest`` via insert_text.

    After attach, channel attest verifies every **required** slug (from the
    staging ``<!--cdp-required-skills:…-->`` authority marker, not rebuilt from
    delivery channels alone) was delivered via attach ∪ inline.
    """
    from claude_bundles.composer_session_skills import attach_session_skills
    from claude_bundles.cowork_skill_delivery import (
        attest_delivery_channels,
        extract_cdp_required_authority,
        parse_cdp_sealed_skill_channels,
    )

    required_authority = extract_cdp_required_authority(text)
    attach_slugs, inline_slugs, rest = parse_cdp_sealed_skill_channels(text)
    if required_authority is not None:
        required = required_authority
    else:
        # Legacy / non-staged prompts: no authority marker — channel union only.
        required = attach_slugs + inline_slugs
    if not required and not attach_slugs and not inline_slugs:
        await page.keyboard.insert_text(text)
        return

    attached: list[str] = []
    if attach_slugs:
        attached = await attach_session_skills(page, attach_slugs, composer=composer)
        attest_delivery_channels(required, attached=attached, inlined=inline_slugs)
    else:
        attest_delivery_channels(required, attached=[], inlined=inline_slugs)

    if rest:
        body = rest
        if not body.startswith(("\n", "\r")):
            body = f"\n\n{body}"
        await composer.click(force=True)
        await page.wait_for_timeout(200)
        await page.keyboard.insert_text(body)


async def send_prompt(page: Page, text: str) -> None:
    """Clear the composer, attach leading slash skills, then click submit.

    Contract: leading ``/<slug>\\n`` tokens name Customize skills to attach via
    ``+`` → Skills → pick-each (not typed); remaining body (incl. hybrid escape
    Use-the lines / ``<skills_inline>``) pastes via insert_text. Fail-closed
    submit (no Enter fallback) via compose attestation / live_discover.
    """
    from claude_bundles.composer_session_skills import require_compose_surface

    require_compose_surface(page)
    composer = await find_composer(page)
    if composer is None:
        raise RuntimeError(f"composer not found on page url={page.url!r}")
    await composer.click(force=True)
    await page.wait_for_timeout(300)
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await _insert_prompt_text(page, text, composer=composer)
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


async def _compose_model_selected(
    page: Page,
    model: str,
    *,
    compose_url: str | None = None,
    project_uuid: str | None = None,
    ensure_cowork_auto: bool = True,
) -> dict:
    """Land compose, select picker model, recover from chrome detours / warm CSE."""
    if project_uuid:
        await goto_fresh_compose(page, project_uuid=project_uuid)
    else:
        url = compose_url or "https://claude.ai/new"
        await goto_fresh_compose(
            page,
            compose_url=url,
            ensure_cowork_auto=ensure_cowork_auto,
        )
    composer = await find_composer(page)
    if composer is not None:
        await composer.click(force=True)
        await page.wait_for_timeout(800)
    model_info = await select_model(page, model)
    if not model_info.get("ok"):
        return model_info
    # Model picker clicks can land on settings / scheduled-task — re-land + re-pick.
    if "/new" not in (page.url or "") and "/cowork/" not in (page.url or ""):
        if project_uuid:
            await goto_fresh_compose(page, project_uuid=project_uuid)
        else:
            await goto_fresh_compose(
                page,
                compose_url=compose_url or "https://claude.ai/new",
                ensure_cowork_auto=ensure_cowork_auto,
            )
        model_info = await select_model(page, model)
        if not model_info.get("ok"):
            return model_info
    # Warm CSE/chat retains the prior family — fresh dispatch must use /new.
    if in_active_chat(page.url or "") and not await picker_attests_request(page, model):
        if project_uuid:
            await goto_fresh_compose(page, project_uuid=project_uuid)
        else:
            await goto_fresh_compose(
                page,
                compose_url=compose_url or "https://claude.ai/new",
                ensure_cowork_auto=ensure_cowork_auto,
            )
        model_info = await select_model(page, model)
    return model_info


async def project_ask_on_page(
    page: Page,
    prompt: str,
    *,
    project_uuid: str,
    model: str = "opus-5",
    delete_after: bool = True,
    timeout_s: int = 360,
    min_growth: int = 50,
    min_body: int = 40,
    archive_path: str | None = None,
    execution_id: str | None = None,
    on_harvest: Callable[[dict], Awaitable[None]] | None = None,
    expected_size: ExpectedSize = "auto",
    harvest_source: HarvestSource = "auto",
    download_output: bool = False,
) -> ProjectAskResult:
    """Run one sealed ask on an existing Playwright page."""
    dest = project_url(project_uuid)
    try:
        model_info = await _compose_model_selected(
            page,
            model,
            project_uuid=project_uuid,
        )
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
            on_harvest=on_harvest,
        )
        body = strip_thinking_prefix(state.get("body") or "")
        attested = _attest_model(model, state, model_info)
        harvest_provenance: HarvestProvenance | None = None
        try:
            harvest = await resolve_harvest_body(
                page,
                body,
                harvest_source=harvest_source,
                expected_size=expected_size,
                download_output=download_output,
                cortex_files_root=cortex_files_root_from_env(),
            )
        except OutputDownloadError as exc:
            # Refuse the archive, keep the transcript — see OutputDownloadError.
            return ProjectAskResult(
                ok=False,
                body=exc.chat_body,
                url=str(state.get("url") or page.url),
                project_uuid=project_uuid,
                project_url=dest,
                model=model_info,
                body_len=len(exc.chat_body),
                delete_after=None,
                error=str(exc),
                attested_model=attested,
                harvest_provenance=None,
            )
        archive_body = harvest.content
        harvest_provenance = harvest.provenance
        archive_uri = None
        if archive_path:
            try:
                archive_uri = archive_harvest(
                    body=archive_body,
                    url=str(state.get("url") or page.url),
                    project_uuid=project_uuid,
                    model=model_info,
                    attested_model=attested,
                    archive_path=archive_path,
                    execution_id=execution_id,
                )
            except HarvestArchiveError as exc:
                return ProjectAskResult(
                    ok=False,
                    body=archive_body,
                    url=str(state.get("url") or page.url),
                    project_uuid=project_uuid,
                    project_url=dest,
                    model=model_info,
                    body_len=len(archive_body),
                    delete_after=None,
                    error=str(exc),
                    attested_model=attested,
                    harvest_provenance=None,
                )
        elif delete_after:
            # Fable MUST: delete only after archive — refuse if no path provided
            return ProjectAskResult(
                ok=False,
                body=archive_body,
                url=str(state.get("url") or page.url),
                project_uuid=project_uuid,
                project_url=dest,
                model=model_info,
                body_len=len(archive_body),
                delete_after=None,
                error="archive_path required before delete (archive-before-delete bind)",
                attested_model=attested,
            )
        delete_result = None
        if delete_after and archive_uri:
            delete_result = await delete_chat_if_active(page, return_to=dest)
        return ProjectAskResult(
            ok=True,
            body=archive_body,
            url=str(state.get("url") or page.url),
            project_uuid=project_uuid,
            project_url=dest,
            model=model_info,
            body_len=len(archive_body),
            delete_after=delete_result,
            archive_uri=archive_uri,
            attested_model=attested,
            harvest_provenance=harvest_provenance,
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
    model: str = "opus-5",
    delete_after: bool = True,
    cdp_url: str = DEFAULT_CDP_URL,
    timeout_s: int = 360,
    min_growth: int = 50,
    min_body: int = 40,
    archive_path: str | None = None,
    execution_id: str | None = None,
    on_harvest: Callable[[dict], Awaitable[None]] | None = None,
    expected_size: ExpectedSize = "auto",
    harvest_source: HarvestSource = "auto",
    download_output: bool = False,
) -> ProjectAskResult:
    """Connect CDP, run one sealed ask, disconnect."""
    pw, _browser, ctx, _page0 = await connect_cdp(cdp_url)
    try:
        page = await pick_chat_page(ctx, prefer_url_substr="/new")
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
            on_harvest=on_harvest,
            expected_size=expected_size,
            harvest_source=harvest_source,
            download_output=download_output,
        )
    finally:
        await pw.stop()
