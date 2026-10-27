"""CSE harvest scrape helpers — Python loading predicate, URL tab pick, cadence wait.

Used by ``cse_session_harvest`` for attached and open-on-demand harvest paths.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chat_harvest.archive import ArchiveConflictError, ArchiveRefusalError
from chat_harvest.chrome import is_chrome_only, substantive_reply_body
from chat_harvest.cse_persist import (
    archive_fields_from_conflict,
    archive_fields_from_refusal,
    persist_cse_dom_harvest,
)
from claude_bundles.cowork_output_download import (
    HarvestBody,
    cortex_files_root_from_env,
    resolve_harvest_body,
)
from claude_bundles.cse_turns_harvest import harvest_turns
from claude_bundles.cse_url import normalize_cse_url
from claude_bundles.overload_only_harvest import is_error_banner_only_harvest
from durable_io.atomic import durable_write_bytes, durable_write_text

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

_SESSION_UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def _session_id_from_harvest_filename(filename: str | None) -> str:
    """Extract Cowork session UUID from a downloaded Output filename."""
    if not filename:
        return "unknown"
    match = _SESSION_UUID_RE.search(filename)
    if match:
        return match.group(1)
    return Path(filename).stem


def _merge_manifest_field(existing: object, new: object) -> object:
    """Keep a prior manifest value when the new harvest omitted it."""
    if new not in (None, ""):
        return new
    if existing not in (None, ""):
        return existing
    return new


def persist_cowork_output_raw_transcript(
    body_result: HarvestBody,
    *,
    chat_url: str | None,
    registration_id: str | None,
    cortex_root: Path | None = None,
) -> tuple[str, str] | None:
    """Write Cowork Output bytes and a sidecar manifest under cortex raw transcripts.

    Returns ``(cortex_uri, sha256_hex)`` when a files root is configured, else ``None``.
    """
    root = cortex_root or cortex_files_root_from_env()
    if root is None:
        return None

    raw_bytes = (
        body_result.content_bytes
        if body_result.content_bytes is not None
        else body_result.content.encode("utf-8")
    )
    sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
    session_id = _session_id_from_harvest_filename(body_result.filename)
    raw_filename = (
        Path(body_result.filename).name
        if body_result.filename
        else f"claude-cowork-{session_id}.jsonl"
    )
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    target_dir = root / "notes/system/transcripts/claude-cowork/raw" / date_str
    target_dir.mkdir(parents=True, exist_ok=True)

    raw_abs = target_dir / raw_filename
    revision: int | None = None
    supersedes_uri: str | None = None

    if raw_abs.is_file():
        existing_sha = hashlib.sha256(raw_abs.read_bytes()).hexdigest()
        if existing_sha == sha256_hex:
            pass
        else:
            supersedes_uri = f"cortex://{raw_abs.relative_to(root).as_posix()}"
            revision = 2
            while True:
                rev_raw = target_dir / f"claude-cowork-{session_id}__r{revision}.jsonl"
                if not rev_raw.is_file():
                    raw_abs = rev_raw
                    break
                rev_sha = hashlib.sha256(rev_raw.read_bytes()).hexdigest()
                if rev_sha == sha256_hex:
                    raw_abs = rev_raw
                    break
                revision += 1
            durable_write_bytes(raw_abs, raw_bytes, retain_store_root=root)
    else:
        durable_write_bytes(raw_abs, raw_bytes, retain_store_root=root)

    if revision is not None:
        manifest_abs = target_dir / f"{session_id}__r{revision}.manifest.json"
    else:
        manifest_abs = target_dir / f"{session_id}.manifest.json"

    existing_manifest: dict[str, Any] = {}
    if manifest_abs.is_file():
        try:
            existing_manifest = json.loads(manifest_abs.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing_manifest = {}

    captured_at = datetime.now(UTC).isoformat()
    if (
        existing_manifest.get("sha256") == sha256_hex
        and existing_manifest.get("captured_at")
    ):
        captured_at = str(existing_manifest["captured_at"])

    manifest_dict: dict[str, Any] = {
        "sha256": sha256_hex,
        "bytes": len(raw_bytes),
        "chat_url": _merge_manifest_field(existing_manifest.get("chat_url"), chat_url),
        "registration_id": _merge_manifest_field(
            existing_manifest.get("registration_id"),
            registration_id,
        ),
        "sessionId": session_id,
        "file_uuid": _merge_manifest_field(
            existing_manifest.get("file_uuid"),
            body_result.file_uuid,
        ),
        "captured_at": captured_at,
        "route": _merge_manifest_field(existing_manifest.get("route"), body_result.route),
    }
    if supersedes_uri is not None:
        manifest_dict["supersedes"] = supersedes_uri

    durable_write_text(
        manifest_abs,
        json.dumps(manifest_dict, indent=2) + "\n",
        retain_store_root=root,
    )
    return (f"cortex://{raw_abs.relative_to(root).as_posix()}", sha256_hex)


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
    *,
    chat_url: str | None = None,
) -> HarvestResponse | None:
    """Attempt auto/output-file harvest; refuse while loading or on banner-only auto."""
    if req.source not in {"output-file", "auto"}:
        return None
    try:
        preview = enrich_dom(await harvest_turns(page, limit=1))
        if (
            preview.get("loading")
            or preview.get("in_flight")
            or preview.get("streaming")
        ):
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
        if not substantive_reply_body(body_result.content):
            return None
        post = enrich_dom(await harvest_turns(page, limit=1))
        if post.get("loading") or post.get("in_flight") or post.get("streaming"):
            return None
        if req.source == "auto" and is_error_banner_only_harvest(body_result.content):
            return None
        ack = classify_ack(
            body_result.content,
            marker=req.marker,
            successor_birth_id=req.successor_birth_id,
        )
        archive_uri: str | None = None
        archive_sha256: str | None = None
        if body_result.provenance == "output-file":
            resolved_chat_url = (
                chat_url
                or req.chat_url
                or str((provenance or {}).get("chat_url") or "")
            ).strip() or None
            resolved_registration_id = (
                req.registration_id
                or str((provenance or {}).get("registration_id") or "")
            ).strip() or None
            persisted = persist_cowork_output_raw_transcript(
                body_result,
                chat_url=resolved_chat_url,
                registration_id=resolved_registration_id,
            )
            if persisted is not None:
                archive_uri, archive_sha256 = persisted
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
            archive_uri=archive_uri,
            archive_sha256=archive_sha256,
        )
    except Exception:
        if req.source == "output-file":
            return HarvestResponse(outcome="unreachable", reason="output_file_miss")
        return None


def _dom_turn_dicts(dom: dict[str, Any]) -> list[dict[str, object]]:
    return [
        {
            "author": row["author"],
            "text": row["text"],
            "ordinal": row.get("ordinal"),
        }
        for row in dom.get("turns") or []
    ]


def enrich_with_messages_archive(
    response: HarvestResponse,
    *,
    chat_url: str | None,
    page_url: str | None,
    dom: dict[str, Any] | None = None,
) -> HarvestResponse:
    """Persist DOM turns to ``ulg.continuity.messages/1`` when harvest succeeded."""
    if response.outcome != "harvested" or response.content_provenance != "cse-dom":
        return response
    if not response.turns:
        return response
    url = (chat_url or page_url or "").strip()
    if not url:
        return response
    dom_turns = _dom_turn_dicts(dom) if dom is not None else _dom_turn_dicts(
        {"turns": [t.model_dump() for t in response.turns]}
    )
    try:
        archive_uri, archive_sha256, alignment, turn_count, message_count = (
            persist_cse_dom_harvest(
                chat_url=url,
                page_url=page_url,
                dom_turns=dom_turns,
            )
        )
    except ArchiveConflictError as exc:
        return response.model_copy(
            update={
                **archive_fields_from_conflict(exc),
                "turns": response.turns,
                "provenance": response.provenance,
                "chat_url": response.chat_url,
                "coverage": response.coverage,
                "content_provenance": response.content_provenance,
            }
        )
    except ArchiveRefusalError as exc:
        return response.model_copy(
            update={
                **archive_fields_from_refusal(exc),
                "turns": response.turns,
                "provenance": response.provenance,
                "chat_url": response.chat_url,
                "coverage": response.coverage,
                "content_provenance": response.content_provenance,
            }
        )
    return response.model_copy(
        update={
            "archive_uri": archive_uri,
            "archive_sha256": archive_sha256,
            "alignment": alignment,
            "turn_count": turn_count,
            "message_count": message_count,
        }
    )


def _dom_to_harvested(
    dom: dict[str, Any],
    req: HarvestRequest,
    provenance: dict[str, Any] | None,
    *,
    waited_ms: int = 0,
    chat_url: str | None = None,
    page_url: str | None = None,
    persist_archive: bool = True,
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
    last = turns[-1]
    if last.author == "assistant" and is_chrome_only(last.text):
        return HarvestResponse(
            outcome="streaming",
            provenance=provenance,
            streaming=True,
            stop=bool(dom.get("stop")),
            tool_pause=True,
            waited_ms=waited_ms or None,
        )
    ack = classify_ack(
        last.text,
        marker=req.marker,
        successor_birth_id=req.successor_birth_id,
    )
    response = HarvestResponse(
        outcome="harvested",
        ack_class=ack,
        turns=turns,
        truncated=bool(dom.get("truncated")),
        cursor=turns[-1].ordinal,
        content_provenance="cse-dom",
        provenance=provenance,
        waited_ms=waited_ms or None,
        coverage=dom.get("coverage"),
    )
    if persist_archive and not req.metadata_only:
        return enrich_with_messages_archive(
            response,
            chat_url=chat_url,
            page_url=page_url,
            dom=dom,
        )
    return response


async def harvest_with_loading_wait(
    page: Any,
    req: HarvestRequest,
    provenance: dict[str, Any] | None,
    *,
    limit: int,
    chat_url: str | None = None,
    page_url: str | None = None,
) -> HarvestResponse:
    """Scrape once, optionally wait while loading, then classify the harvest outcome."""
    body_resp = await _try_body_harvest(page, req, provenance, chat_url=chat_url)
    if body_resp is not None:
        return body_resp

    try:
        dom = await scrape_once(page, limit=limit, after_turn=req.after_turn)
    except Exception as exc:
        return HarvestResponse(outcome="unreachable", reason=str(exc))

    if not dom.get("loading"):
        return _dom_to_harvested(
            dom,
            req,
            provenance,
            chat_url=chat_url,
            page_url=page_url or str(getattr(page, "url", None) or "") or None,
        )

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
            body_resp = await _try_body_harvest(
                page, req, provenance, chat_url=chat_url
            )
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
            body_resp = await _try_body_harvest(
                page, req, provenance, chat_url=chat_url
            )
            if body_resp is not None:
                body_resp.waited_ms = waited_ms
                return body_resp
        last_loading = bool(dom.get("loading"))
        if loading_transition or not dom.get("loading"):
            return _dom_to_harvested(
                dom,
                req,
                provenance,
                waited_ms=waited_ms,
                chat_url=chat_url,
                page_url=page_url or str(getattr(page, "url", None) or "") or None,
            )

    return _dom_to_harvested(
        dom,
        req,
        provenance,
        waited_ms=waited_ms,
        chat_url=chat_url,
        page_url=page_url or str(getattr(page, "url", None) or "") or None,
    )
