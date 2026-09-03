"""Grok.com full-transcript harvest and paste helpers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_bundles.skills_ui_panel import connect_cdp
from web_chat_relay import grok_session
from web_chat_relay.grok_session import GrokAuthError

from chat_harvest.archive import (
    ArchiveConflictError,
    ArchiveRefusalError,
    archive_chat_transcript,
)
from chat_harvest.models import (
    DEFAULT_RELAY_STATE_FILE,
    ChatHarvestResponse,
    ChatPasteResponse,
    ChatTurn,
    build_harvest_response,
    relay_lock_fresh,
)

FULL_TRANSCRIPT_JS = """
() => {
  const body = (document.body && document.body.innerText) || "";
  const url = location.href;
  const loginWall = /sign in to request access|this chat is private|log into your account/i.test(body)
    || /accounts\\.(x\\.ai|google\\.com)/.test(url);
  const stop = [...document.querySelectorAll("button")].some((b) => {
    const label = ((b.innerText || "") + " " + (b.getAttribute("aria-label") || "")).trim();
    return /^(stop|stop generating)$/i.test(label) || /stop generating/i.test(label);
  });
  const streaming = stop || !!document.querySelector("[aria-busy='true']");
  const nodes = [...document.querySelectorAll(
    "[data-testid='user-message'], [data-testid='assistant-message']"
  )];
  let ordinal = 0;
  const turns = nodes.map((el) => {
    const testid = el.getAttribute("data-testid") || "";
    const author = testid === "user-message" ? "user" : "assistant";
    ordinal += 1;
    return { author, ordinal, text: (el.innerText || "").trim() };
  });
  return { url, login_wall: loginWall, streaming, stop, turns };
}
"""


def _turns_from_dom(raw_turns: list[dict[str, Any]]) -> list[ChatTurn]:
    turns: list[ChatTurn] = []
    for item in raw_turns:
        author = str(item.get("author") or "assistant")
        text = str(item.get("text") or "")
        if author == "assistant":
            text = grok_session.strip_chrome(text)
        turns.append(
            ChatTurn(
                author="user" if author == "user" else "assistant",
                ordinal=int(item.get("ordinal") or len(turns) + 1),
                text=text,
                source="dom",
            )
        )
    return turns


async def scroll_stabilize(page, *, rounds: int = 8, pause_s: float = 0.25) -> None:
    last_height = -1
    for _ in range(rounds):
        height = await page.evaluate("document.body.scrollHeight")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(pause_s)
        if height == last_height:
            break
        last_height = height


async def harvest_full_transcript(page) -> tuple[list[ChatTurn], dict[str, Any]]:
    await scroll_stabilize(page)
    raw = await page.evaluate(FULL_TRANSCRIPT_JS)
    return _turns_from_dom(list(raw.get("turns") or [])), raw


async def _has_grok_cookies(context) -> bool:
    for cookie in await context.cookies():
        domain = str(cookie.get("domain") or "").lstrip(".")
        if domain.endswith("grok.com") or domain.endswith("x.ai"):
            return True
    return False


async def execute_grok_harvest(
    *,
    url: str,
    site: str,
    conversation_id: str,
    cdp_url: str,
    include_turns: str = "none",
    limit: int = 10,
    after_turn: int | None = None,
    supersede: bool = False,
    metadata_only: bool = False,
) -> ChatHarvestResponse:
    if not conversation_id:
        return ChatHarvestResponse(
            outcome="no_conversation",
            site=site,
            conversation_id=conversation_id,
            url=url,
        )

    substr = grok_session.conversation_id_from_url(url) or url
    pw = None
    try:
        pw, _browser, _context, page = await grok_session.attach_grok_page(
            cdp_url=cdp_url, url_substr=substr
        )
        turns, raw = await harvest_full_transcript(page)
        if raw.get("login_wall"):
            return ChatHarvestResponse(
                outcome="unauthenticated",
                site=site,
                conversation_id=conversation_id,
                url=str(raw.get("url") or url),
                code="unauthenticated",
                reason="login wall on grok tab",
            )

        live_url = str(raw.get("url") or url)
        live_id = grok_session.conversation_id_from_url(live_url) or conversation_id
        streaming = bool(raw.get("streaming"))
        if metadata_only:
            return build_harvest_response(
                site=site,
                live_id=live_id,
                live_url=live_url,
                turns=turns,
                streaming=streaming,
                include_turns=include_turns,
                limit=limit,
                after_turn=after_turn,
            )

        harvested_at = datetime.now(UTC).isoformat()
        try:
            archive_uri, archive_sha256 = archive_chat_transcript(
                site,
                live_id,
                live_url,
                turns,
                harvested_at=harvested_at,
                streaming=streaming,
                supersede=supersede,
            )
        except ArchiveConflictError as exc:
            return ChatHarvestResponse(
                outcome="archive_conflict",
                site=site,
                conversation_id=live_id,
                url=live_url,
                turn_count=len(turns),
                existing_sha256=exc.existing_sha256,
                conflict=exc.detail,
                code="archive_conflict",
                reason=str(exc),
            )
        except ArchiveRefusalError as exc:
            return ChatHarvestResponse(
                outcome="refused",
                site=site,
                conversation_id=live_id,
                url=live_url,
                turn_count=len(turns),
                code=exc.code,
                reason=exc.reason,
            )

        return build_harvest_response(
            site=site,
            live_id=live_id,
            live_url=live_url,
            turns=turns,
            streaming=streaming,
            include_turns=include_turns,
            limit=limit,
            after_turn=after_turn,
            harvested_at=harvested_at,
            archive_uri=archive_uri,
            archive_sha256=archive_sha256,
        )
    except GrokAuthError as exc:
        return ChatHarvestResponse(
            outcome="no_tab",
            site=site,
            conversation_id=conversation_id,
            url=url,
            code="no_tab",
            reason=str(exc),
        )
    finally:
        if pw is not None:
            await pw.stop()


async def execute_grok_paste(
    *,
    url: str,
    prompt_text: str,
    cdp_url: str,
    grant: str = "operator",
    state_file: Path = DEFAULT_RELAY_STATE_FILE,
) -> ChatPasteResponse:
    if grant not in ("explicit", "operator"):
        return ChatPasteResponse(
            ok=False, code="grant_required", reason="grant must be explicit or operator"
        )
    if relay_lock_fresh(state_file):
        return ChatPasteResponse(
            ok=False,
            code="relay_lock_fresh",
            reason="grok-claude relay lockfile is fresh (<120s)",
        )

    substr = grok_session.conversation_id_from_url(url) or url
    pw = None
    try:
        try:
            pw, _browser, context, page = await grok_session.attach_grok_page(
                cdp_url=cdp_url, url_substr=substr
            )
        except GrokAuthError:
            pw, _browser, context, _page0 = await connect_cdp(cdp_url)
            if not await _has_grok_cookies(context):
                await pw.stop()
                return ChatPasteResponse(
                    ok=False,
                    code="no_tab",
                    reason="no grok tab and no grok/x.ai cookies on CDP context",
                )
            page = await context.new_page()
            await page.goto("https://grok.com/")

        await grok_session.paste_and_send(page, prompt_text.strip())
        await grok_session.wait_idle(page)
        live_url = page.url or url
        live_id = grok_session.conversation_id_from_url(live_url)
        turns, raw = await harvest_full_transcript(page)
        if raw.get("login_wall"):
            return ChatPasteResponse(
                ok=False,
                site="grok",
                conversation_id=live_id,
                url=live_url,
                code="unauthenticated",
                reason="login wall after paste",
            )

        pasted_at = datetime.now(UTC).isoformat()
        archive_uri = archive_sha256 = None
        if live_id:
            archive_uri, archive_sha256 = archive_chat_transcript(
                "grok",
                live_id,
                live_url,
                turns,
                harvested_at=pasted_at,
                streaming=False,
            )
        return ChatPasteResponse(
            ok=True,
            site="grok",
            conversation_id=live_id,
            url=live_url,
            archive_uri=archive_uri,
            archive_sha256=archive_sha256,
            send_verified=True,
            pasted_at=pasted_at,
        )
    except (GrokAuthError, TimeoutError) as exc:
        return ChatPasteResponse(ok=False, code="unreachable", reason=str(exc))
    finally:
        if pw is not None:
            await pw.stop()
