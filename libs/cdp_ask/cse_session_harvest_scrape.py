"""CSE harvest scrape helpers — Python loading predicate, URL tab pick, cadence wait.

Used by ``cse_session_harvest`` for attached and open-on-demand harvest paths.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
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
from continuity_tape.extract_jsonl import extract_turns_from_jsonl_bytes
from durable_io.atomic import (
    PreImageMismatchError,
    durable_write_bytes,
    durable_write_text,
)

from cdp_ask.cse_session_ack import classify_ack
from cdp_ask.cse_session_models import (
    CseSessionTurn,
    HarvestRequest,
    HarvestResponse,
    OutputCardSummary,
)

logger = logging.getLogger(__name__)

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
_NATIVE_ID_REJECT_RE = re.compile(r"__r\d+$")
_HOUSE_PROVENANCE_KEYS = (
    "lane_thread_proven",
    "lane_thread_claim",
    "parent_thread_proven",
    "parent_thread_claim",
)
_RAW_SOURCE = "claude-ai"
_RAW_KIND = "raw"
_MESSAGES_KIND = "messages"


@dataclass(frozen=True)
class CoworkOutputPersistResult:
    """Raw + messages artifact digests after a Cowork Output persist."""

    raw_uri: str
    raw_sha256: str
    messages_path: str
    messages_sha256: str


def _session_id_from_harvest_filename(filename: str | None) -> str:
    """Extract Cowork session UUID from a downloaded Output filename."""
    if not filename:
        return "unknown"
    match = _SESSION_UUID_RE.search(filename)
    if match:
        return match.group(1)
    return Path(filename).stem


def _native_id_from_filename(filename: str | None) -> str | None:
    """Return session UUID native_id or ``None`` when the token is rejected."""
    if not filename or not filename.lower().endswith(".jsonl"):
        return None
    match = _SESSION_UUID_RE.search(filename)
    if not match:
        return None
    native_id = match.group(1)
    if _NATIVE_ID_REJECT_RE.search(native_id) or native_id.endswith(".manifest"):
        return None
    return native_id


def _house_from_provenance(provenance: dict[str, Any] | None) -> str:
    """Resolve transcript house from lane/parent thread provenance keys."""
    for key in _HOUSE_PROVENANCE_KEYS:
        raw = str((provenance or {}).get(key) or "").strip()
        if not raw:
            continue
        token = raw.removeprefix("agent-bus:").strip()
        if token:
            return token
    return "_nohouse"


def _manifest_rel(manifest_abs: Path, root: Path) -> str:
    return manifest_abs.relative_to(root).as_posix()


def _raw_candidate_paths(target_dir: Path, native_id: str) -> list[tuple[Path, int | None]]:
    """Base raw path plus ``__r{N}`` revision paths in allocation order."""
    ordered: list[tuple[Path, int | None]] = [(target_dir / f"{native_id}.jsonl", None)]
    rev = 2
    while rev < 50:
        ordered.append((target_dir / f"{native_id}__r{rev}.jsonl", rev))
        rev += 1
    return ordered


def _cas_write_raw(
    target_dir: Path,
    native_id: str,
    raw_bytes: bytes,
    *,
    root: Path,
) -> tuple[Path, int | None, bool]:
    """CAS write-once raw bytes; return path, revision, and whether bytes reconciled."""
    sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
    for raw_abs, revision in _raw_candidate_paths(target_dir, native_id):
        try:
            durable_write_bytes(
                raw_abs,
                raw_bytes,
                expected_pre_image="",
                retain_store_root=root,
            )
            return raw_abs, revision, False
        except PreImageMismatchError:
            if not raw_abs.is_file():
                continue
            if hashlib.sha256(raw_abs.read_bytes()).hexdigest() == sha256_hex:
                return raw_abs, revision, True
    raise RuntimeError(f"Cowork raw transcript CAS exhausted revisions for {native_id}")


def _persist_cowork_messages_projection(
    *,
    root: Path,
    house: str,
    native_id: str,
    date_str: str,
    raw_bytes: bytes,
    raw_sha256: str,
    raw_abs: Path,
    revision: int | None,
    captured_at: str,
    chat_url: str | None,
    registration_id: str | None,
    route: str | None,
    prev_messages_manifest_rel: str | None,
) -> tuple[str, str]:
    """Project tool-stripped messages beside the raw Cowork transcript tree."""
    messages_dir = (
        root / "notes/system/transcripts" / house / _RAW_SOURCE / _MESSAGES_KIND / date_str
    )
    messages_dir.mkdir(parents=True, exist_ok=True)
    if revision is None:
        messages_abs = messages_dir / f"{native_id}.messages.json"
        messages_manifest_abs = messages_dir / f"{native_id}.manifest.json"
    else:
        messages_abs = messages_dir / f"{native_id}__r{revision}.messages.json"
        messages_manifest_abs = (
            messages_dir / f"{native_id}__r{revision}.manifest.json"
        )

    envelope = extract_turns_from_jsonl_bytes(
        raw_bytes,
        tools="none",
        transcript_id=native_id,
        session_id=native_id,
        observed_at=captured_at,
        surface="claude_ai",
    )
    projection = envelope.model_dump(mode="json", by_alias=True)
    meta = projection.setdefault("meta", {})
    if isinstance(meta, dict):
        meta["raw_sha256"] = raw_sha256
    projection["raw_sha256"] = raw_sha256
    messages_text = json.dumps(projection, indent=2) + "\n"
    messages_bytes = messages_text.encode("utf-8")
    messages_sha256 = hashlib.sha256(messages_bytes).hexdigest()
    durable_write_text(messages_abs, messages_text, retain_store_root=root)

    messages_supersedes: str | None = None
    if revision is not None and prev_messages_manifest_rel:
        messages_supersedes = prev_messages_manifest_rel

    messages_manifest: dict[str, Any] = {
        "sha256": messages_sha256,
        "bytes": len(messages_bytes),
        "captured_at": captured_at,
        "house": house,
        "source": _RAW_SOURCE,
        "kind": _MESSAGES_KIND,
        "native_id": native_id,
        "sessionId": native_id,
        "raw_sha256": raw_sha256,
        "raw_rel_path": raw_abs.relative_to(root).as_posix(),
        "chat_url": chat_url,
        "registration_id": registration_id,
        "route": route,
        "supersedes": messages_supersedes,
    }
    durable_write_text(
        messages_manifest_abs,
        json.dumps(messages_manifest, indent=2) + "\n",
        retain_store_root=root,
    )
    return (
        f"cortex://{messages_abs.relative_to(root).as_posix()}",
        messages_sha256,
    )


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
    provenance: dict[str, Any] | None = None,
    cortex_root: Path | None = None,
) -> CoworkOutputPersistResult | None:
    """Write Cowork Output bytes and a sidecar manifest under cortex raw transcripts.

    Layout: ``notes/system/transcripts/<house>/claude-ai/raw/<YYYY-MM-DD>/``.
    Returns raw and messages artifact digests when a files root is configured.
    """
    root = cortex_root or cortex_files_root_from_env()
    if root is None:
        return None

    native_id = _native_id_from_filename(body_result.filename)
    if native_id is None:
        return None

    raw_bytes = (
        body_result.content_bytes
        if body_result.content_bytes is not None
        else body_result.content.encode("utf-8")
    )
    sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
    house = _house_from_provenance(provenance)
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    target_dir = (
        root / "notes/system/transcripts" / house / _RAW_SOURCE / _RAW_KIND / date_str
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    base_manifest_abs = target_dir / f"{native_id}.manifest.json"
    prev_manifest_rel = _manifest_rel(base_manifest_abs, root)

    raw_abs, revision, reconciled = _cas_write_raw(
        target_dir,
        native_id,
        raw_bytes,
        root=root,
    )
    if revision is None:
        manifest_abs = base_manifest_abs
    else:
        manifest_abs = target_dir / f"{native_id}__r{revision}.manifest.json"

    existing_manifest: dict[str, Any] = {}
    manifest_corrupt = not manifest_abs.is_file()
    if manifest_abs.is_file():
        try:
            existing_manifest = json.loads(manifest_abs.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing_manifest = {}
            manifest_corrupt = True

    captured_at = datetime.now(UTC).isoformat()
    if (
        reconciled
        and existing_manifest.get("sha256") == sha256_hex
        and existing_manifest.get("captured_at")
    ):
        captured_at = str(existing_manifest["captured_at"])

    supersedes: str | None = None
    if revision is not None:
        supersedes = prev_manifest_rel
    elif reconciled and (manifest_corrupt or not manifest_abs.is_file()):
        supersedes = prev_manifest_rel

    manifest_dict: dict[str, Any] = {
        "sha256": sha256_hex,
        "bytes": len(raw_bytes),
        "captured_at": captured_at,
        "house": house,
        "source": _RAW_SOURCE,
        "kind": _RAW_KIND,
        "native_id": native_id,
        "sessionId": native_id,
        "chat_url": _merge_manifest_field(existing_manifest.get("chat_url"), chat_url),
        "registration_id": _merge_manifest_field(
            existing_manifest.get("registration_id"),
            registration_id,
        ),
        "file_uuid": _merge_manifest_field(
            existing_manifest.get("file_uuid"),
            body_result.file_uuid,
        ),
        "route": _merge_manifest_field(existing_manifest.get("route"), body_result.route),
        "supersedes": _merge_manifest_field(existing_manifest.get("supersedes"), supersedes),
    }

    durable_write_text(
        manifest_abs,
        json.dumps(manifest_dict, indent=2) + "\n",
        retain_store_root=root,
    )

    base_messages_manifest = (
        root
        / "notes/system/transcripts"
        / house
        / _RAW_SOURCE
        / _MESSAGES_KIND
        / date_str
        / f"{native_id}.manifest.json"
    )
    messages_path, messages_sha256 = _persist_cowork_messages_projection(
        root=root,
        house=house,
        native_id=native_id,
        date_str=date_str,
        raw_bytes=raw_bytes,
        raw_sha256=sha256_hex,
        raw_abs=raw_abs,
        revision=revision,
        captured_at=captured_at,
        chat_url=str(manifest_dict.get("chat_url") or "") or None,
        registration_id=str(manifest_dict.get("registration_id") or "") or None,
        route=str(manifest_dict.get("route") or "") or None,
        prev_messages_manifest_rel=_manifest_rel(base_messages_manifest, root),
    )
    return CoworkOutputPersistResult(
        raw_uri=f"cortex://{raw_abs.relative_to(root).as_posix()}",
        raw_sha256=sha256_hex,
        messages_path=messages_path,
        messages_sha256=messages_sha256,
    )


def _selected_output_card_summary(
    panel_filename: str,
    body_result: HarvestBody,
    persisted: CoworkOutputPersistResult,
) -> OutputCardSummary:
    """Build the post-harvest card row for the selected Outputs file."""
    raw_bytes = (
        body_result.content_bytes
        if body_result.content_bytes is not None
        else body_result.content.encode("utf-8")
    )
    return OutputCardSummary(
        filename=panel_filename,
        file_uuid=body_result.file_uuid,
        bytes=len(raw_bytes),
        raw_sha256=persisted.raw_sha256,
        messages_path=persisted.messages_path,
        messages_sha256=persisted.messages_sha256,
    )


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


async def _enumerate_output_cards(
    page: Any,
    *,
    probe_bytes: bool,
) -> list[OutputCardSummary]:
    """List Outputs panel filenames; optionally download each row for byte counts."""
    from claude_bundles.cowork_output_download import download_cowork_output_by_filename
    from claude_bundles.cowork_output_preview import list_cowork_output_card_filenames

    filenames = await list_cowork_output_card_filenames(page)
    cards: list[OutputCardSummary] = []
    for name in filenames:
        if not probe_bytes:
            cards.append(OutputCardSummary(filename=name))
            continue
        downloaded = await download_cowork_output_by_filename(page, name)
        if downloaded is None:
            cards.append(OutputCardSummary(filename=name, bytes=0))
            continue
        cards.append(
            OutputCardSummary(
                filename=downloaded.filename,
                file_uuid=downloaded.file_uuid,
                bytes=len(downloaded.content_bytes),
            )
        )
    return cards


def _cards_matching_marker(
    cards: list[OutputCardSummary],
    marker: str | None,
) -> list[OutputCardSummary]:
    token = (marker or "").strip()
    if not token:
        return list(cards)
    return [card for card in cards if token in card.filename]


def _harvest_body_from_download(downloaded: Any) -> HarvestBody:
    from claude_bundles.cowork_output_download import CLASSIC_OUTPUT_DOWNLOAD_ROUTE

    return HarvestBody(
        content=downloaded.content,
        provenance="output-file",
        filename=downloaded.filename,
        content_bytes=downloaded.content_bytes,
        route=getattr(downloaded, "route", CLASSIC_OUTPUT_DOWNLOAD_ROUTE),
        file_uuid=getattr(downloaded, "file_uuid", None),
    )


async def _try_output_file_card_harvest(
    page: Any,
    req: HarvestRequest,
    provenance: dict[str, Any] | None,
    *,
    chat_url: str | None,
    allow_miss_terminal: bool,
) -> HarvestResponse | None:
    """Outputs-panel card listing, marker selection, and optional metadata-only probe."""
    cards = await _enumerate_output_cards(
        page,
        probe_bytes=req.metadata_only,
    )
    if req.metadata_only:
        return HarvestResponse(
            outcome="harvested",
            content_provenance="metadata_only",
            cards=cards,
            provenance=provenance,
        )
    if not cards:
        return None

    marker = (req.marker or "").strip() or None
    if len(cards) > 1 and not marker:
        return HarvestResponse(
            outcome="disambiguation",
            reason="output_card_disambiguation",
            cards=cards,
            provenance=provenance,
        )

    pool = _cards_matching_marker(cards, marker) if marker else cards
    if marker and not pool:
        return HarvestResponse(
            outcome="unreachable",
            reason="output_marker_miss",
            cards=cards,
            provenance=provenance,
        )
    if len(pool) > 1:
        return HarvestResponse(
            outcome="disambiguation",
            reason="output_card_disambiguation",
            cards=pool,
            provenance=provenance,
        )

    from claude_bundles.cowork_output_download import download_cowork_output_by_filename

    downloaded = await download_cowork_output_by_filename(page, pool[0].filename)
    if downloaded is None or not downloaded.content.strip():
        if allow_miss_terminal:
            return HarvestResponse(outcome="unreachable", reason="output_file_miss")
        return None
    body_result = _harvest_body_from_download(downloaded)
    if not substantive_reply_body(body_result.content):
        if allow_miss_terminal:
            return HarvestResponse(outcome="unreachable", reason="output_file_miss")
        return None

    post = enrich_dom(await harvest_turns(page, limit=1))
    if post.get("loading") or post.get("in_flight") or post.get("streaming"):
        return None

    ack = classify_ack(
        body_result.content,
        marker=req.marker,
        successor_birth_id=req.successor_birth_id,
    )
    archive_uri: str | None = None
    archive_sha256: str | None = None
    resolved_chat_url = (
        chat_url or req.chat_url or str((provenance or {}).get("chat_url") or "")
    ).strip() or None
    resolved_registration_id = (
        req.registration_id or str((provenance or {}).get("registration_id") or "")
    ).strip() or None
    persisted = persist_cowork_output_raw_transcript(
        body_result,
        chat_url=resolved_chat_url,
        registration_id=resolved_registration_id,
        provenance=provenance,
    )
    selected_cards: list[OutputCardSummary] = list(cards)
    if persisted is not None:
        archive_uri = persisted.raw_uri
        archive_sha256 = persisted.raw_sha256
        selected_cards = [
            _selected_output_card_summary(pool[0].filename, body_result, persisted)
        ]
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
        content_provenance="output-file",
        provenance=provenance,
        archive_uri=archive_uri,
        archive_sha256=archive_sha256,
        cards=selected_cards,
    )


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
    allow_miss_terminal: bool = True,
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
        if req.source == "output-file":
            card_resp = await _try_output_file_card_harvest(
                page,
                req,
                provenance,
                chat_url=chat_url,
                allow_miss_terminal=allow_miss_terminal,
            )
            if card_resp is not None:
                return card_resp
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
        selected_cards: list[OutputCardSummary] = []
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
            persisted: CoworkOutputPersistResult | None = None
            try:
                persisted = persist_cowork_output_raw_transcript(
                    body_result,
                    chat_url=resolved_chat_url,
                    registration_id=resolved_registration_id,
                    provenance=provenance,
                )
            except Exception:
                logger.warning(
                    "Cowork output-file persist failed; harvest continues without archive",
                    exc_info=True,
                )
                persisted = None
            if persisted is not None:
                archive_uri = persisted.raw_uri
                archive_sha256 = persisted.raw_sha256
                panel_name = Path(body_result.filename or "").name or (
                    body_result.filename or "cowork-output"
                )
                selected_cards = [
                    _selected_output_card_summary(
                        panel_name,
                        body_result,
                        persisted,
                    )
                ]
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
            cards=selected_cards,
        )
    except Exception:
        if req.source == "output-file":
            if allow_miss_terminal:
                return HarvestResponse(outcome="unreachable", reason="output_file_miss")
            return None
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
    body_resp = await _try_body_harvest(
        page,
        req,
        provenance,
        chat_url=chat_url,
        allow_miss_terminal=False,
    )
    if body_resp is not None:
        return body_resp

    if req.source == "output-file":
        waited_ms = 0
        deadline = time.monotonic() + OPEN_ON_DEMAND_INCOMPLETE_WAIT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(OPEN_ON_DEMAND_INCOMPLETE_POLL_S)
            waited_ms += int(OPEN_ON_DEMAND_INCOMPLETE_POLL_S * 1000)
            try:
                dom = await scrape_once(page, limit=limit, after_turn=req.after_turn)
            except Exception as exc:
                return HarvestResponse(outcome="unreachable", reason=str(exc))
            if dom.get("in_flight") or dom.get("streaming"):
                continue
            body_resp = await _try_body_harvest(
                page,
                req,
                provenance,
                chat_url=chat_url,
                allow_miss_terminal=False,
            )
            if body_resp is not None:
                body_resp.waited_ms = waited_ms
                return body_resp
        body_resp = await _try_body_harvest(
            page,
            req,
            provenance,
            chat_url=chat_url,
            allow_miss_terminal=True,
        )
        if body_resp is not None:
            body_resp.waited_ms = waited_ms
            return body_resp
        return HarvestResponse(
            outcome="unreachable",
            reason="output_file_miss",
            waited_ms=waited_ms or None,
        )

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
                page,
                req,
                provenance,
                chat_url=chat_url,
                allow_miss_terminal=False,
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
                page,
                req,
                provenance,
                chat_url=chat_url,
                allow_miss_terminal=False,
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
