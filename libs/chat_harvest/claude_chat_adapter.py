"""Claude.ai /chat/ full-transcript harvest and paste helpers."""

from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from claude_bundles.chat_reply_wait import harvest_assistant, wait_assistant_reply
from claude_bundles.project_ask import find_composer, strip_thinking_prefix
from claude_bundles.skills_ui_panel import connect_cdp
from playwright.async_api import Error as PlaywrightError
from universal_logging import get_logger

from chat_harvest.archive import (
    ArchiveConflictError,
    ArchiveRefusalError,
    archive_chat_transcript,
)
from chat_harvest.grok_adapter import scroll_stabilize
from chat_harvest.models import (
    ChatHarvestResponse,
    ChatPasteResponse,
    ChatTurn,
    ClassifyRefuse,
    build_harvest_response,
    classify_chat_url,
)

logger = get_logger(__name__)

# fmt: off
FULL_TRANSCRIPT_JS = "()=>{const url=location.href;const loginWall=/\\/login/i.test(url)||/\\/logout/i.test(url);const stop=[...document.querySelectorAll('button')].some(b=>/^(stop|stop generating)$/i.test(((b.innerText||'')+' '+(b.getAttribute('aria-label')||'')).trim()));const streaming=stop||!!document.querySelector(\"[aria-busy='true']\");const seen=new Set();const nodes=[];for(const sel of [\"[data-testid='user-message']\",\"[data-testid='assistant-message']\",\"div[class*='font-claude']\"]){for(const el of document.querySelectorAll(sel)){if(!seen.has(el)){seen.add(el);nodes.push(el);}}}nodes.sort((a,b)=>(a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING)?-1:1);let ordinal=0;const turns=nodes.map(el=>{const testid=el.getAttribute('data-testid')||'';ordinal+=1;return{author:testid==='user-message'?'user':'assistant',ordinal,text:(el.innerText||'').trim()};});return{url,login_wall:loginWall,streaming,stop,turns};}"
# fmt: on


def _turns_from_dom(raw_turns: list[dict[str, Any]]) -> list[ChatTurn]:
    turns: list[ChatTurn] = []
    for item in raw_turns:
        author = str(item.get("author") or "assistant")
        text = (
            strip_thinking_prefix(str(item.get("text") or ""))
            if author == "assistant"
            else str(item.get("text") or "")
        )
        turns.append(
            ChatTurn(
                author="user" if author == "user" else "assistant",
                ordinal=int(item.get("ordinal") or len(turns) + 1),
                text=text,
                source="dom",
            )
        )
    return turns


def _cse_refuse(url: str) -> ClassifyRefuse | None:
    classified = classify_chat_url(url, site="claude")
    if isinstance(classified, ClassifyRefuse) and classified.code == "use_cse_session":
        return classified
    if "/cowork/cse_" in (url or "").lower():
        return ClassifyRefuse(
            code="use_cse_session", reason="Cowork CSE URLs must use cse_session"
        )
    return None


def _live_id(live_url: str, fallback: str = "") -> str:
    classified = classify_chat_url(live_url)
    return (
        fallback
        if isinstance(classified, ClassifyRefuse)
        else (classified.conversation_id or fallback)
    )


async def _poll_dom(page, *, max_attempts: int = 20, pause_s: float = 0.25) -> dict[str, Any]:
    """Wait for login wall or turns after open-on-demand navigation."""
    raw: dict[str, Any] = {}
    for _ in range(max_attempts):
        raw = await page.evaluate(FULL_TRANSCRIPT_JS)
        if raw.get("login_wall") or raw.get("turns"):
            return raw
        await asyncio.sleep(pause_s)
    return await page.evaluate(FULL_TRANSCRIPT_JS)


async def _resolve_page(
    context,
    conversation_id: str,
    *,
    paste: bool,
    open_url: str = "",
):
    pages = [p for p in context.pages if "claude.ai" in (p.url or "")]
    non_cse = [p for p in pages if "/cowork/cse_" not in (p.url or "").lower()]
    if pages and not non_cse and not open_url:
        return None, "use_cse_session", False
    if not paste:
        for page in non_cse:
            if conversation_id and f"/chat/{conversation_id}" in (page.url or ""):
                await page.bring_to_front()
                return page, None, False
        if not open_url:
            return None, "no_tab", False
        page = await context.new_page()
        try:
            await page.goto(open_url, wait_until="domcontentloaded")
        except Exception:
            with suppress(Exception):
                await page.close()
            raise
        return page, None, True
    scored = []
    for p in non_cse:
        u = p.url or ""
        s = (
            100
            if ("/chat/" in u or u.rstrip("/").endswith("/new") or "/new?" in u)
            else 0
        ) + (50 if conversation_id and conversation_id in u else 0)
        if s >= 100:
            scored.append((s, p))
    if scored:
        page = max(scored, key=lambda x: x[0])[1]
        if "/cowork/cse_" in (page.url or "").lower():
            return None, "use_cse_session", False
        await page.bring_to_front()
        return page, None, False
    page = await context.new_page()
    await page.goto("https://claude.ai/new")
    return page, None, True


async def harvest_full_transcript(page) -> tuple[list[ChatTurn], dict[str, Any]]:
    await scroll_stabilize(page)
    raw = await page.evaluate(FULL_TRANSCRIPT_JS)
    return _turns_from_dom(list(raw.get("turns") or [])), raw


# fmt: off
async def execute_claude_harvest(
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
    if (refuse := _cse_refuse(url)) is not None:
        return ChatHarvestResponse(
            outcome="refused",
            site=site,
            conversation_id=conversation_id,
            url=url,
            code=refuse.code,
            reason=refuse.reason,
        )
    if not conversation_id:
        return ChatHarvestResponse(
            outcome="no_conversation", site=site, conversation_id="", url=url
        )
    pw = None
    page = None
    minted = False
    try:
        pw, _browser, context, _page = await connect_cdp(cdp_url)
        page, err, minted = await _resolve_page(
            context,
            conversation_id,
            paste=False,
            open_url=url,
        )
        if err == "use_cse_session":
            return ChatHarvestResponse(
                outcome="refused",
                site=site,
                conversation_id=conversation_id,
                url=url,
                code="use_cse_session",
                reason="only CSE tabs",
            )
        if page is None:
            return ChatHarvestResponse(
                outcome="no_tab",
                site=site,
                conversation_id=conversation_id,
                url=url,
                code="no_tab",
                reason="no matching chat tab",
            )
        if minted:
            priming = await _poll_dom(page)
            if priming.get("login_wall"):
                return ChatHarvestResponse(
                    outcome="unauthenticated",
                    site=site,
                    conversation_id=conversation_id,
                    url=str(priming.get("url") or url),
                    code="unauthenticated",
                    reason="login wall",
                    opened_on_demand=True,
                )
            if not priming.get("turns"):
                return ChatHarvestResponse(
                    outcome="unreachable",
                    site=site,
                    conversation_id=conversation_id,
                    url=str(priming.get("url") or url),
                    code="incomplete_dom",
                    reason="no turns after open-on-demand navigation",
                    opened_on_demand=True,
                )
        turns, raw = await harvest_full_transcript(page)
        if raw.get("login_wall"):
            return ChatHarvestResponse(
                outcome="unauthenticated",
                site=site,
                conversation_id=conversation_id,
                url=str(raw.get("url") or url),
                code="unauthenticated",
                reason="login wall",
                opened_on_demand=minted,
            )
        live_url, live_id, streaming = (
            str(raw.get("url") or url),
            _live_id(str(raw.get("url") or url), conversation_id),
            bool(raw.get("streaming")),
        )
        kw = dict(
            site=site,
            live_id=live_id,
            live_url=live_url,
            turns=turns,
            streaming=streaming,
            include_turns=include_turns,
            limit=limit,
            after_turn=after_turn,
        )
        if metadata_only:
            return build_harvest_response(**kw, opened_on_demand=minted)
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
                opened_on_demand=minted,
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
                opened_on_demand=minted,
            )
        return build_harvest_response(
            **kw,
            harvested_at=harvested_at,
            archive_uri=archive_uri,
            archive_sha256=archive_sha256,
            opened_on_demand=minted,
        )
    except (RuntimeError, PlaywrightError) as exc:
        logger.warning("claude harvest unreachable: %s", exc)
        return ChatHarvestResponse(
            outcome="unreachable",
            site=site,
            conversation_id=conversation_id,
            url=url,
            code="unreachable",
            reason=str(exc),
            opened_on_demand=minted,
        )
    finally:
        if minted and page is not None:
            with suppress(Exception):
                await page.close()
        if pw is not None:
            await pw.stop()
# fmt: on


# fmt: off
async def execute_claude_paste(
    *,
    url: str,
    prompt_text: str,
    cdp_url: str,
    grant: str = "operator",
) -> ChatPasteResponse:
    if grant not in ("explicit", "operator"):
        return ChatPasteResponse(ok=False, code="grant_required", reason="grant must be explicit or operator")
    if (refuse := _cse_refuse(url)) is not None:
        return ChatPasteResponse(ok=False, code=refuse.code, reason=refuse.reason)
    pw = None
    try:
        pw, _browser, context, _page = await connect_cdp(cdp_url)
        page, err, _minted = await _resolve_page(context, _live_id(url), paste=True)
        if err == "use_cse_session":
            return ChatPasteResponse(ok=False, code="use_cse_session", reason="only CSE tabs")
        if page is None or "/cowork/cse_" in (page.url or "").lower():
            return ChatPasteResponse(ok=False, code=("use_cse_session" if page is not None else "no_tab"), reason="CSE or missing chat tab")
        pre_send = await harvest_assistant(page)
        composer = await find_composer(page)
        if composer is None:
            return ChatPasteResponse(ok=False, code="unreachable", reason=f"composer not found url={page.url!r}")
        await composer.click(force=True)
        await page.keyboard.insert_text(prompt_text.strip())
        btn = page.get_by_role("button", name=re.compile(r"^Send message$", re.I))
        for i in range(await btn.count()):
            el = btn.nth(i)
            if await el.is_visible():
                await el.click()
                break
        else:
            raise RuntimeError("Send message button not visible")
        await wait_assistant_reply(page, before=pre_send)
        live_url, live_id = page.url or url, _live_id(page.url or url)
        turns, raw = await harvest_full_transcript(page)
        if raw.get("login_wall"):
            return ChatPasteResponse(ok=False, site="claude", conversation_id=live_id, url=live_url, code="unauthenticated", reason="login wall")
        pasted_at = datetime.now(UTC).isoformat()
        archive_uri = archive_sha256 = None
        if live_id:
            archive_uri, archive_sha256 = archive_chat_transcript("claude", live_id, live_url, turns, harvested_at=pasted_at, streaming=False)
        return ChatPasteResponse(ok=True, site="claude", conversation_id=live_id, url=live_url, archive_uri=archive_uri, archive_sha256=archive_sha256, send_verified=True, pasted_at=pasted_at)
    except (RuntimeError, TimeoutError) as exc:
        return ChatPasteResponse(ok=False, code="unreachable", reason=str(exc))
    finally:
        if pw is not None:
            await pw.stop()
# fmt: on
