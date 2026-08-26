"""CSE harvest scrape helpers — Python loading predicate, URL tab pick, cadence wait.

Used by ``cse_session_harvest`` for attached and open-on-demand harvest paths.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from claude_bundles.cowork_output_download import resolve_harvest_body
from claude_bundles.cse_turns_harvest import harvest_turns
from claude_bundles.cse_url import normalize_cse_url
from claude_bundles.overload_only_harvest import is_error_banner_only_harvest

from cdp_ask.cse_session_ack import classify_ack
from cdp_ask.cse_session_models import CseSessionTurn, HarvestRequest, HarvestResponse

_CSE_PATH_MARKER = "/cowork/cse_"

# Compose shell titles — same vocabulary as compose_attest / chat_cowork_mode.
SHELL_TITLES: frozenset[str] = frozenset(
    {
        "",
        "Claude",
        "New chat - Claude",
        "New task - Claude",
    }
)

OPEN_ON_DEMAND_INCOMPLETE_WAIT_S = 12.0
OPEN_ON_DEMAND_INCOMPLETE_POLL_S = 0.5
_FULL_SCRAPE_INTERVAL_S = 2.0


def is_shell_title(title: str | None) -> bool:
    """True when the document title is a bare compose shell, not a named session."""
    token = (title or "").strip()
    if token in SHELL_TITLES:
        return True
    lowered = token.casefold()
    return lowered in {shell.casefold() for shell in SHELL_TITLES}


def is_loading(dom: dict[str, Any]) -> bool:
    """True when the page shows spinner/aria-busy or a compose shell title."""
    if dom.get("spinner") or dom.get("aria_busy"):
        return True
    return is_shell_title(str(dom.get("title") or ""))


def compute_incomplete_dom(dom: dict[str, Any]) -> bool:
    """Incomplete DOM := empty turns, not streaming, and still loading."""
    turns = dom.get("turns") or []
    streaming = bool(dom.get("streaming"))
    loading = dom.get("loading")
    if loading is None:
        loading = is_loading(dom)
    return not turns and not streaming and bool(loading)


def enrich_dom(dom: dict[str, Any]) -> dict[str, Any]:
    """Attach Python-side loading and incomplete_dom to a raw harvest_turns payload."""
    dom["loading"] = is_loading(dom)
    dom["incomplete_dom"] = compute_incomplete_dom(dom)
    return dom


async def pick_page_for_chat_url(ctx: Any, chat_url: str, *, fallback: Any) -> Any:
    """Prefer the open tab whose URL normalizes to *chat_url*; else *fallback*."""
    target = normalize_cse_url(chat_url)
    if not target:
        return fallback
    for page in ctx.pages:
        url = page.url or ""
        if _CSE_PATH_MARKER not in url:
            continue
        if normalize_cse_url(url) == target:
            return page
    return fallback


async def scrape_once(
    page: Any,
    *,
    limit: int,
    after_turn: int | None,
) -> dict[str, Any]:
    """One bounded DOM read with loading and incomplete_dom computed in Python."""
    dom = await harvest_turns(page, limit=limit, after_turn=after_turn)
    return enrich_dom(dom)


async def _try_body_harvest(
    page: Any,
    req: HarvestRequest,
    provenance: dict[str, Any] | None,
) -> HarvestResponse | None:
    """Attempt auto/output-file harvest; refuse while loading or on banner-only auto."""
    if req.source not in {"output-file", "auto"}:
        return None
    try:
        preview = enrich_dom(await harvest_turns(page, limit=1))
        if preview.get("loading"):
            return None
        chat_body = ""
        if preview.get("turns"):
            chat_body = str(preview["turns"][-1].get("text") or "")
        body_result = await resolve_harvest_body(
            page,
            chat_body,
            harvest_source=req.source,
            expected_size="auto",
            download_output=False,
        )
        if not body_result or not body_result.content:
            return None
        post = enrich_dom(await harvest_turns(page, limit=1))
        if post.get("loading"):
            return None
        if req.source == "auto" and is_error_banner_only_harvest(body_result.content):
            return None
        ack = classify_ack(
            body_result.content,
            marker=req.marker,
            successor_birth_id=req.successor_birth_id,
        )
        return HarvestResponse(
            outcome="harvested",
            ack_class=ack,
            turns=[
                CseSessionTurn(
                    author="assistant",
                    text=body_result.content,
                    source="output-file",
                )
            ],
            content_provenance=str(body_result.provenance),
            provenance=provenance,
        )
    except Exception:
        if req.source == "output-file":
            return HarvestResponse(outcome="unreachable", reason="output_file_miss")
        return None


def _dom_to_harvested(
    dom: dict[str, Any],
    req: HarvestRequest,
    provenance: dict[str, Any] | None,
    *,
    waited_ms: int = 0,
) -> HarvestResponse:
    """Map an enriched DOM scrape to the appropriate harvest outcome."""
    if dom.get("in_flight"):
        return HarvestResponse(
            outcome="streaming",
            provenance=provenance,
            streaming=bool(dom.get("streaming")),
            stop=bool(dom.get("stop")),
            tool_pause=bool(dom.get("tool_pause")),
            waited_ms=waited_ms or None,
        )
    if dom.get("incomplete_dom"):
        return HarvestResponse(
            outcome="incomplete_dom",
            reason="loading",
            provenance=provenance,
            waited_ms=waited_ms or None,
        )
    turns = [
        CseSessionTurn(
            author=row["author"],
            timestamp=row.get("timestamp"),
            text=row["text"],
            source="cse-dom",
            ordinal=row.get("ordinal"),
        )
        for row in dom.get("turns") or []
    ]
    if not turns:
        return HarvestResponse(
            outcome="no_reply_yet",
            reason="settled_empty",
            provenance=provenance,
            waited_ms=waited_ms or None,
        )
    ack = classify_ack(
        turns[-1].text,
        marker=req.marker,
        successor_birth_id=req.successor_birth_id,
    )
    return HarvestResponse(
        outcome="harvested",
        ack_class=ack,
        turns=turns,
        truncated=bool(dom.get("truncated")),
        cursor=turns[-1].ordinal,
        content_provenance="cse-dom",
        provenance=provenance,
        waited_ms=waited_ms or None,
    )


async def harvest_with_loading_wait(
    page: Any,
    req: HarvestRequest,
    provenance: dict[str, Any] | None,
    *,
    limit: int,
) -> HarvestResponse:
    """Scrape once, optionally wait while loading, then classify the harvest outcome."""
    body_resp = await _try_body_harvest(page, req, provenance)
    if body_resp is not None:
        return body_resp

    try:
        dom = await scrape_once(page, limit=limit, after_turn=req.after_turn)
    except Exception as exc:
        return HarvestResponse(outcome="unreachable", reason=str(exc))

    if not dom.get("loading"):
        return _dom_to_harvested(dom, req, provenance)

    waited_ms = 0
    deadline = time.monotonic() + OPEN_ON_DEMAND_INCOMPLETE_WAIT_S
    last_full = time.monotonic()
    last_loading = True

    while time.monotonic() < deadline:
        await asyncio.sleep(OPEN_ON_DEMAND_INCOMPLETE_POLL_S)
        waited_ms += int(OPEN_ON_DEMAND_INCOMPLETE_POLL_S * 1000)

        now = time.monotonic()
        loading_transition = False
        do_full = (now - last_full) >= _FULL_SCRAPE_INTERVAL_S
        if do_full:
            body_resp = await _try_body_harvest(page, req, provenance)
            if body_resp is not None:
                body_resp.waited_ms = waited_ms
                return body_resp
            last_full = now

        try:
            dom = await scrape_once(page, limit=limit, after_turn=req.after_turn)
        except Exception as exc:
            return HarvestResponse(outcome="unreachable", reason=str(exc))

        if last_loading and not dom.get("loading"):
            loading_transition = True
            body_resp = await _try_body_harvest(page, req, provenance)
            if body_resp is not None:
                body_resp.waited_ms = waited_ms
                return body_resp
        last_loading = bool(dom.get("loading"))
        if loading_transition or not dom.get("loading"):
            return _dom_to_harvested(dom, req, provenance, waited_ms=waited_ms)

    return _dom_to_harvested(dom, req, provenance, waited_ms=waited_ms)
