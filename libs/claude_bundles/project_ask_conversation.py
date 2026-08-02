"""N-turn CDP consult on one claude.ai chat (default picker: Opus 5)."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from claude_bundles.chat_reply_wait import harvest_assistant, wait_assistant_reply
from claude_bundles.chat_session_hygiene import (
    delete_chat_if_active,
    pick_chat_page,
)
from claude_bundles.cowork_output_download import (
    ExpectedSize,
    HarvestSource,
    OutputDownloadError,
    cortex_files_root_from_env,
    resolve_harvest_body,
)
from claude_bundles.project_ask import (
    ProjectAskResult,
    _compose_model_selected,
    project_ask_on_page,
    send_prompt,
    strip_thinking_prefix,
)
from claude_bundles.project_chrome import project_url
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL, connect_cdp

# Prefer BREAK_IN / MONITOR unique markers over shared headers (TYPE: BREAK_IN).
_UNIQUE_MARKER_RE = re.compile(r"#\d+-unique:\s*\S+")

_TRANSCRIPT_MARKER_JS = """
() => {
  const selectors = [
    '[data-testid="user-message"]',
    '[data-testid="human-turn"]',
    '[data-testid*="user"]',
    'div[class*="font-user"]',
  ];
  let count = 0;
  let last = '';
  const seen = new Set();
  for (const sel of selectors) {
    for (const el of document.querySelectorAll(sel)) {
      if (seen.has(el)) continue;
      seen.add(el);
      const t = (el.innerText || '').trim();
      if (t) {
        count += 1;
        last = t;
      }
    }
  }
  return { count, last_len: last.length, last_snippet: last.slice(0, 400) };
}
"""


def verification_marker(prompt: str) -> str:
    """Distinctive substring that must appear in the CSE after paste.

    Count-only DOM growth is not delivery proof (reattach races + wrong-packet
    re-pastes). Prefer ``#N-unique:…``; else a mid-body slice so shared headers
    alone cannot verify.
    """
    text = (prompt or "").strip()
    if not text:
        return ""
    match = _UNIQUE_MARKER_RE.search(text)
    if match:
        return match.group(0)
    if len(text) >= 120:
        return text[40:120].strip()
    return text[:80]


def _transcript_grew(before: dict, after: dict) -> bool:
    return after.get("count", 0) > before.get("count", 0) or (
        after.get("last_len", 0) > before.get("last_len", 0)
    )


async def send_followup_paste_half(page, prompt: str) -> dict:
    """Paste *prompt* into a live CSE and verify the user turn — no reply wait.

    Send-only contract: calls ``send_prompt`` then **fail-closed** on marker
    presence (not count growth alone). Does **not** call
    ``wait_assistant_reply`` or ``resolve_harvest_body``. Mid-turn
    ``streaming_at_paste`` is reported (allow + report); callers decide whether
    to retry later.
    """
    import time

    from claude_bundles.chat_reply_wait import harvest_assistant

    url = page.url or ""
    marker = verification_marker(prompt)
    before = await page.evaluate(_TRANSCRIPT_MARKER_JS)
    await send_prompt(page, prompt)
    after = await page.evaluate(_TRANSCRIPT_MARKER_JS)
    body_has = False
    if marker:
        body_has = bool(
            await page.evaluate(
                "(m) => ((document.body && document.body.innerText) || '').includes(m)",
                marker,
            )
        )
    in_snippet = bool(marker) and marker in (after.get("last_snippet") or "")
    send_verified = bool(marker) and _transcript_grew(before, after) and (
        in_snippet or body_has
    )
    state = await harvest_assistant(page)
    pasted_at = time.time()
    return {
        "send_verified": bool(send_verified),
        "streaming_at_paste": bool(state.get("streaming")),
        "url": str(state.get("url") or page.url or url),
        "pasted_at": pasted_at,
        "verification_marker": marker,
        "error": None if send_verified else "send_unverified",
    }


async def project_followup_on_page(
    page,
    prompt: str,
    *,
    project_uuid: str,
    timeout_s: int = 360,
    min_growth: int = 50,
    min_body: int = 40,
    on_harvest: Callable[[dict], Awaitable[None]] | None = None,
    expected_size: ExpectedSize = "auto",
    harvest_source: HarvestSource = "auto",
    download_output: bool = False,
) -> ProjectAskResult:
    """Send another turn on the current chat. No navigate."""
    dest = project_url(project_uuid) if project_uuid else "https://claude.ai/new"
    try:
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
                project_uuid=project_uuid or "",
                project_url=dest,
                model={},
                body_len=len(exc.chat_body),
                delete_after=None,
                error=str(exc),
                harvest_provenance=None,
            )
        return ProjectAskResult(
            ok=True,
            body=harvest.content,
            url=str(state.get("url") or page.url),
            project_uuid=project_uuid or "",
            project_url=dest,
            model={"ok": True, "step": "followup"},
            body_len=len(harvest.content),
            delete_after=None,
            harvest_provenance=harvest.provenance,
        )
    except Exception as exc:  # noqa: BLE001
        return ProjectAskResult(
            ok=False,
            body="",
            url=page.url or "",
            project_uuid=project_uuid or "",
            project_url=dest,
            model={},
            body_len=0,
            delete_after=None,
            error=str(exc),
        )


async def run_project_conversation(
    prompts: list[str],
    *,
    project_uuid: str = "",
    compose_url: str | None = None,
    model: str = "opus-5",
    delete_after: bool = True,
    cdp_url: str = DEFAULT_CDP_URL,
    timeout_s: int = 600,
    min_growth: int = 80,
    min_body: int = 200,
    ensure_cowork_auto: bool = True,
    on_harvest: Callable[[dict], Awaitable[None]] | None = None,
    expected_size: ExpectedSize = "auto",
    harvest_source: HarvestSource = "auto",
    download_output: bool = False,
) -> list[ProjectAskResult]:
    """N-turn consult on one chat. First opens compose; later turns follow up."""
    if not prompts:
        raise ValueError("prompts required")
    pw, _browser, ctx, _page0 = await connect_cdp(cdp_url)
    results: list[ProjectAskResult] = []
    try:
        page = await pick_chat_page(ctx, prefer_url_substr="/new")
        if project_uuid:
            first = await project_ask_on_page(
                page,
                prompts[0],
                project_uuid=project_uuid,
                model=model,
                delete_after=False,
                timeout_s=timeout_s,
                min_growth=min_growth,
                min_body=min_body,
                on_harvest=on_harvest,
                expected_size=expected_size,
                harvest_source=harvest_source,
                download_output=download_output,
            )
        else:
            url = compose_url or "https://claude.ai/new"
            model_info = await _compose_model_selected(
                page,
                model,
                compose_url=url,
                ensure_cowork_auto=ensure_cowork_auto,
            )
            if not model_info.get("ok"):
                return [
                    ProjectAskResult(
                        ok=False,
                        body="",
                        url=page.url,
                        project_uuid="",
                        project_url=url,
                        model=model_info,
                        body_len=0,
                        delete_after=None,
                        error=f"model select failed: {model_info}",
                    )
                ]
            before = await harvest_assistant(page)
            await send_prompt(page, prompts[0])
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
                return [
                    ProjectAskResult(
                        ok=False,
                        body=exc.chat_body,
                        url=str(state.get("url") or page.url),
                        project_uuid="",
                        project_url=url,
                        model=model_info,
                        body_len=len(exc.chat_body),
                        delete_after=None,
                        error=str(exc),
                        harvest_provenance=None,
                    )
                ]
            first = ProjectAskResult(
                ok=True,
                body=harvest.content,
                url=str(state.get("url") or page.url),
                project_uuid="",
                project_url=url,
                model=model_info,
                body_len=len(harvest.content),
                delete_after=None,
                harvest_provenance=harvest.provenance,
            )
        results.append(first)
        if not first.ok:
            return results

        for prompt in prompts[1:]:
            nxt = await project_followup_on_page(
                page,
                prompt,
                project_uuid=project_uuid,
                timeout_s=timeout_s,
                min_growth=min_growth,
                min_body=min_body,
                on_harvest=on_harvest,
                expected_size=expected_size,
                harvest_source=harvest_source,
                download_output=download_output,
            )
            results.append(nxt)
            if not nxt.ok:
                break

        if delete_after and results and results[-1].ok:
            return_to = (
                project_url(project_uuid) if project_uuid else "https://claude.ai/new"
            )
            delete_result = await delete_chat_if_active(page, return_to=return_to)
            last = results[-1]
            results[-1] = ProjectAskResult(
                ok=last.ok,
                body=last.body,
                url=last.url,
                project_uuid=last.project_uuid,
                project_url=last.project_url,
                model=last.model,
                body_len=last.body_len,
                delete_after=delete_result,
                error=last.error,
                archive_uri=last.archive_uri,
                attested_model=last.attested_model,
                harvest_provenance=last.harvest_provenance,
            )
        return results
    finally:
        await pw.stop()
