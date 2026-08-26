"""Resolve a harvest identity (URL, execution id, cse id) to a Cowork URL."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from claude_bundles import cdp_registry
from claude_bundles.cse_url import normalize_cse_url

from cdp_ask.cse_session_models import HarvestRequest
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.runner import verify_harvest_root

_URL_LINE = re.compile(
    r"^-\s+url:\s+`(https://claude\.ai/(?:cowork/)?cse_[^`]+)`",
    re.MULTILINE,
)
_EXEC_LINE = re.compile(r"^-\s+execution_id:\s+`([^`]+)`", re.MULTILINE)
_CSE_TOKEN = re.compile(r"^(?:https://claude\.ai/(?:cowork/)?)?(cse_[A-Za-z0-9]+)/?$")


def cse_url_from_token(raw: str) -> str | None:
    """Accept a full Cowork URL or a bare ``cse_…`` token."""
    text = (raw or "").strip()
    match = _CSE_TOKEN.match(text)
    if match:
        return normalize_cse_url(f"https://claude.ai/cowork/{match.group(1)}")
    if "/cowork/cse_" in text:
        return normalize_cse_url(text)
    return None


def _archive_dir() -> Path:
    return verify_harvest_root() / "notes/system/threads"


def _url_from_archive_text(text: str) -> str | None:
    match = _URL_LINE.search(text)
    if not match:
        return None
    return cse_url_from_token(match.group(1))


def chat_url_from_archives(
    execution_id: str,
    *,
    archive_dir: Path | None = None,
) -> str | None:
    """Find the Cowork URL recorded for a satellite or Stargate execution id."""
    token = (execution_id or "").strip()
    if not token:
        return None
    root = archive_dir if archive_dir is not None else _archive_dir()
    if not root.is_dir():
        return None
    for path in root.glob(f"cdp-ask-archive-*-{token}.md"):
        found = _url_from_archive_text(path.read_text(encoding="utf-8")[:4000])
        if found:
            return found
    for path in root.glob("cdp-ask-archive-*.md"):
        head = path.read_text(encoding="utf-8")[:4000]
        exec_match = _EXEC_LINE.search(head)
        if exec_match and exec_match.group(1) == token:
            return _url_from_archive_text(head)
        if len(token) >= 16 and token in head:
            found = _url_from_archive_text(head)
            if found:
                return found
    return None


def chat_url_from_provenance(execution_id: str) -> str | None:
    """Latest provenance episode whose correlation_id matches the token."""
    token = (execution_id or "").strip()
    if not token:
        return None
    from claude_bundles.cse_provenance import read_episodes

    for episode in reversed(read_episodes()):
        if episode.correlation_id == token and episode.chat_url:
            return cse_url_from_token(episode.chat_url)
    return None


def satellite_id_from_inflight(stargate_execution_id: str) -> str | None:
    """Read durable Stargate→satellite map (survives cdp_ask recycle)."""
    token = (stargate_execution_id or "").strip()
    if not token:
        return None
    data_dir = Path(os.getenv("DATA_DIR", str(Path.home() / ".gateway"))).expanduser()
    db = data_dir / "stargate-cdp-generate-inflight.db"
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(db, timeout=2.0)
        try:
            row = conn.execute(
                "SELECT satellite_execution_id FROM cdp_inflight_leg "
                "WHERE execution_id=?",
                (token,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    sat = (row[0] or "").strip()
    return sat or None


async def resolve_harvest_chat_url(
    req: HarvestRequest,
    store: ExecutionStore,
) -> str | None:
    """URL first, then live store, registry, archive — never require a live attach."""
    for raw in (req.chat_url, req.execution_id, req.registration_id):
        from_token = cse_url_from_token(raw or "")
        if from_token:
            return from_token
    if req.registration_id:
        bound = cdp_registry.chat_url_for_registration(req.registration_id)
        if bound:
            return normalize_cse_url(bound)
    if req.execution_id:
        rec = await store.get(req.execution_id)
        if rec is not None and rec.registration_id:
            bound = cdp_registry.chat_url_for_registration(rec.registration_id)
            if bound:
                return normalize_cse_url(bound)
        archived = chat_url_from_archives(req.execution_id)
        if archived:
            return archived
        from_prov = chat_url_from_provenance(req.execution_id)
        if from_prov:
            return from_prov
        satellite = satellite_id_from_inflight(req.execution_id)
        if satellite:
            rec = await store.get(satellite)
            if rec is not None and rec.registration_id:
                bound = cdp_registry.chat_url_for_registration(rec.registration_id)
                if bound:
                    return normalize_cse_url(bound)
            archived = chat_url_from_archives(satellite)
            if archived:
                return archived
            from_prov = chat_url_from_provenance(satellite)
            if from_prov:
                return from_prov
    return None
