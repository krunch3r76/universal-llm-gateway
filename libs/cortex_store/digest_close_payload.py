"""Derive session-close digest payloads from cited journal entities.

Post-201 seam: load dated ``document:journal-*`` entities, segment entry text,
merge with explicit caller ``digest=``, enqueue via ``dispatch_digest_background``.
Fail-open — never blocks session close.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from .db import cortex_conn, decode_row, query
from .digest_dispatch import dispatch_digest_background
from .digest_segment import segment_journal_entry
from .rag_resolver import _source_uri_to_absolute_path

logger = get_logger("cortex-api.digest_close_payload")

_JOURNAL_PREFIX = "document:journal-"
_DATED_ENTRY_RE = re.compile(
    r"^document:journal-(?:entry-\d+|\d{4}-\d{2}-\d{2})$"
)
_REJECT_IDS = frozenset({"document:journal-bridge-spec"})


def is_valid_dated_journal_entity(entity_id: str) -> bool:
    """True for dated journal entry IDs; rejects bridge-spec glob false positives."""
    if entity_id in _REJECT_IDS:
        return False
    if not entity_id.startswith(_JOURNAL_PREFIX):
        return False
    return _DATED_ENTRY_RE.match(entity_id) is not None


def _entry_date_from_entity(entity_id: str, entity: dict[str, Any]) -> str:
    for source in (entity_id, str(entity.get("name") or "")):
        match = re.search(r"(\d{4}-\d{2}-\d{2})", source)
        if match:
            return match.group(1)
    attrs = entity.get("attributes")
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except json.JSONDecodeError:
            attrs = {}
    if isinstance(attrs, dict):
        for key in ("entry_date", "date"):
            val = attrs.get(key)
            if val:
                return str(val)[:10]
    return "1970-01-01"


def _read_journal_uri(uri: str) -> str | None:
    import os

    if not uri.startswith("journal://"):
        return None
    base = os.environ.get("JOURNAL_BRIDGE_URL", "").strip().rstrip("/")
    if not base:
        return None
    rest = uri[len("journal://") :].lstrip("/")
    url = f"{base}/{rest}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.read().decode("utf-8")
    except (OSError, urllib.error.URLError, TimeoutError):
        logger.warning("journal bridge read failed for %s", uri, exc_info=True)
        return None


def _read_text_from_source_uri(source_uri: str) -> str | None:
    if source_uri.startswith("journal://"):
        return _read_journal_uri(source_uri)
    try:
        path = _source_uri_to_absolute_path(source_uri)
        if path.startswith("http://") or path.startswith("https://"):
            return None
        file_path = Path(path)
        if file_path.is_file():
            return file_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        logger.warning("failed to read source_uri %s", source_uri, exc_info=True)
    return None


def load_journal_entry_text(
    entity_id: str,
) -> tuple[str, str, str | None] | None:
    """Load entry markdown and date for a journal entity. Fail-open → None."""
    try:
        with cortex_conn() as conn:
            rows = query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
            if not rows:
                return None
            entity = decode_row(rows[0], json_fields=frozenset({"aliases", "attributes"}))
            source_uri = entity.get("source_uri")
            if not source_uri:
                return None
            text = _read_text_from_source_uri(str(source_uri))
            if not text or not text.strip():
                return None
            entry_date = _entry_date_from_entity(entity_id, entity)
            return text, entry_date, str(source_uri)
    except Exception:
        logger.warning("load_journal_entry_text failed for %s", entity_id, exc_info=True)
        return None


def derive_payloads_from_entity_ids(entity_ids: list[str]) -> list[dict[str, Any]]:
    """Build per-section digest payloads for valid dated journal entities."""
    payloads: list[dict[str, Any]] = []
    for entity_id in entity_ids:
        if not is_valid_dated_journal_entity(entity_id):
            continue
        loaded = load_journal_entry_text(entity_id)
        if loaded is None:
            continue
        text, entry_date, journal_uri = loaded
        for seg in segment_journal_entry(text, entry_date=entry_date):
            payload: dict[str, Any] = {
                "journal_entity_id": entity_id,
                "entry_anchor": seg.entry_anchor,
                "entry_text": seg.entry_text,
            }
            if journal_uri:
                payload["journal_uri"] = journal_uri
            payloads.append(payload)
    return payloads


def _digest_triple_key(payload: dict[str, Any]) -> tuple[str, str] | None:
    journal_entity_id = payload.get("journal_entity_id")
    entry_anchor = payload.get("entry_anchor")
    if journal_entity_id and entry_anchor:
        return str(journal_entity_id), str(entry_anchor)
    return None


def dispatch_close_digests(
    *,
    entity_ids: list[str],
    explicit_digest: dict[str, Any] | None,
    session_id: str | None,
) -> None:
    """Merge auto-derived and explicit digest payloads; enqueue fail-open."""
    try:
        auto_payloads = derive_payloads_from_entity_ids(entity_ids)
        auto_by_key = {
            key: payload
            for payload in auto_payloads
            if (key := _digest_triple_key(payload)) is not None
        }
        explicit_key = (
            _digest_triple_key(explicit_digest)
            if isinstance(explicit_digest, dict)
            else None
        )

        for key, payload in auto_by_key.items():
            if key == explicit_key and isinstance(explicit_digest, dict):
                dispatch_digest_background(explicit_digest, session_id=session_id)
            else:
                dispatch_digest_background(payload, session_id=session_id)

        if explicit_key is not None and explicit_key not in auto_by_key:
            dispatch_digest_background(explicit_digest, session_id=session_id)
        elif explicit_digest is not None and explicit_key is None:
            dispatch_digest_background(explicit_digest, session_id=session_id)
    except Exception:
        logger.warning(
            "dispatch_close_digests failed open for session %s",
            session_id,
            exc_info=True,
        )
