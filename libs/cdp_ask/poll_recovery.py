"""Recover terminal poll snapshots when the in-memory execution row is gone.

Cowork can finish while ``ExecutionStore`` loses the satellite id (process
recycle, idle TTL reap). Poll clients then see HTTP 404. This module rebuilds
a status-bearing snapshot from durable archives or a bounded CSE harvest.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from claude_bundles.chat_model_select import current_model_label
from claude_bundles.project_ask import (
    _archive_body_section,
    archive_harvest,
    read_archive_execution_id,
)
from chat_harvest.chrome import is_chrome_only, substantive_reply_body

from cdp_ask.cse_session_harvest_identity import (
    chat_url_from_archives,
    chat_url_from_provenance,
    resolve_harvest_chat_url,
    satellite_id_from_inflight,
)
from cdp_ask.cse_session_harvest import harvest_page
from cdp_ask.cse_session_models import HarvestRequest, HarvestResponse
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.runner import verify_harvest_root

_URL_LINE = re.compile(
    r"^-\s+url:\s+`(https://claude\.ai/(?:cowork/)?cse_[^`]+)`",
    re.MULTILINE,
)
_ATTESTED_LINE = re.compile(r"^-\s+attested_model:\s+`([^`]+)`", re.MULTILINE)
_STARGATE_LINE = re.compile(r"^-\s+stargate_execution_id:\s+`([^`]+)`", re.MULTILINE)

_RECOVERY_MIN_RETRY_S = 60.0
_recovery_last_attempt: dict[str, float] = {}


def correlation_tokens(execution_id: str) -> list[str]:
    """Satellite id, Stargate id, and inflight alias — deduped in probe order."""
    token = (execution_id or "").strip()
    if not token:
        return []
    out: list[str] = [token]
    satellite = satellite_id_from_inflight(token)
    if satellite and satellite not in out:
        out.insert(0, satellite)
    for sat in list(out):
        stargate = stargate_id_from_satellite(sat)
        if stargate and stargate not in out:
            out.append(stargate)
    return out


def stargate_id_from_satellite(satellite_execution_id: str) -> str | None:
    """Reverse durable Stargate→satellite map when polling by satellite id."""
    token = (satellite_execution_id or "").strip()
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
                "SELECT execution_id FROM cdp_inflight_leg "
                "WHERE satellite_execution_id=?",
                (token,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    stargate = (row[0] or "").strip()
    return stargate or None


def _archive_dir() -> Path:
    return verify_harvest_root() / "notes/system/threads"


def find_archive_path(token: str, *, archive_dir: Path | None = None) -> Path | None:
    """Locate a harvest archive file for a satellite or Stargate execution id."""
    needle = (token or "").strip()
    if not needle:
        return None
    root = archive_dir if archive_dir is not None else _archive_dir()
    if not root.is_dir():
        return None
    direct = root / f"cdp-ask-archive-{needle}.md"
    if direct.is_file():
        return direct
    for path in root.glob(f"cdp-ask-archive-*-{needle}.md"):
        if path.is_file():
            return path
    for path in root.glob("cdp-ask-archive-*.md"):
        head = path.read_text(encoding="utf-8")[:4000]
        exec_match = re.search(r"^- execution_id: `([^`]+)`", head, re.MULTILINE)
        if exec_match and exec_match.group(1) == needle:
            return path
        stargate_match = _STARGATE_LINE.search(head)
        if stargate_match and stargate_match.group(1) == needle:
            return path
    return None


def _path_to_cortex_uri(path: Path) -> str:
    root = verify_harvest_root().resolve()
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(root)
        return f"cortex://{rel.as_posix()}"
    except ValueError:
        return f"file://{resolved}"


def snapshot_from_archive_path(
    path: Path,
    *,
    execution_id: str,
) -> dict[str, Any] | None:
    """Project one on-disk harvest archive into a poll-shaped dict."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    body = _archive_body_section(text).strip()
    if not body or is_chrome_only(body):
        return None
    url_match = _URL_LINE.search(text)
    url = url_match.group(1) if url_match else ""
    attested_match = _ATTESTED_LINE.search(text)
    attested = attested_match.group(1) if attested_match else None
    stamped = read_archive_execution_id(str(path)) or execution_id
    return {
        "execution_id": stamped,
        "status": "completed",
        "ok": True,
        "archive_uri": _path_to_cortex_uri(path),
        "body": body,
        "body_len": len(body),
        "url": url,
        "attested_model": attested,
        "harvest_provenance": "chat",
        "completion_phase": "terminal",
    }


def snapshot_from_archive_token(
    token: str,
    *,
    archive_dir: Path | None = None,
) -> dict[str, Any] | None:
    path = find_archive_path(token, archive_dir=archive_dir)
    if path is None:
        return None
    return snapshot_from_archive_path(path, execution_id=token)


def _recovery_allowed(token: str) -> bool:
    last = _recovery_last_attempt.get(token)
    if last is None:
        return True
    return (time.time() - last) >= _RECOVERY_MIN_RETRY_S


def _mark_recovery_attempt(token: str) -> None:
    _recovery_last_attempt[token] = time.time()


def _body_from_harvest(response: HarvestResponse) -> str:
    for turn in reversed(response.turns):
        text = (turn.text or "").strip()
        if text and substantive_reply_body(text):
            return substantive_reply_body(text)
    return ""


def _default_recovery_archive_path(satellite_id: str) -> str:
    root = verify_harvest_root()
    return str(root / "notes/system/threads" / f"cdp-ask-archive-cdp-recover-{satellite_id}.md")


async def _harvest_chat_to_snapshot(
    *,
    chat_url: str,
    satellite_id: str,
    stargate_execution_id: str | None,
) -> dict[str, Any] | None:
    """Open Cowork, harvest, archive, and return a terminal poll snapshot."""
    from cdp_ask.cse_session_harvest_open import _teardown_opened
    from cdp_ask.followup_reattach import ensure_cse_attached

    req = HarvestRequest(
        chat_url=chat_url,
        execution_id=satellite_id,
        source="auto",
        limit=50,
    )
    outcome = await ensure_cse_attached(
        chat_url,
        holder="cdp-poll-recovery",
        allow_mint=True,
    )
    if not outcome.ok or outcome.page is None:
        return None
    attested = (await current_model_label(outcome.page)).strip() or None
    response: HarvestResponse | None = None
    try:
        response = await harvest_page(
            outcome.page,
            req,
            provenance={"poll_recovery": True},
        )
    finally:
        await _teardown_opened(outcome, response)

    if response.outcome != "harvested":
        return None
    body = _body_from_harvest(response)
    if not body:
        return None
    archive_path = _default_recovery_archive_path(satellite_id)
    try:
        archive_uri = archive_harvest(
            body=body,
            url=chat_url,
            project_uuid="",
            model={},
            attested_model=attested,
            archive_path=archive_path,
            execution_id=satellite_id,
            stargate_execution_id=stargate_execution_id,
        )
    except Exception:
        archive_uri = None
    provenance = response.content_provenance or "cse-dom"
    return {
        "execution_id": satellite_id,
        "status": "completed",
        "ok": True,
        "archive_uri": archive_uri,
        "body": body,
        "body_len": len(body),
        "url": chat_url,
        "attested_model": attested,
        "harvest_provenance": provenance,
        "completion_phase": "terminal",
    }


async def recover_poll_snapshot(
    execution_id: str,
    store: ExecutionStore,
) -> dict[str, Any] | None:
    """Return a terminal poll snapshot when the execution store row is missing."""
    tokens = correlation_tokens(execution_id)
    if not tokens:
        return None

    for token in tokens:
        snap = snapshot_from_archive_token(token)
        if snap is not None:
            return snap

    chat_url: str | None = None
    for token in tokens:
        chat_url = chat_url_from_archives(token) or chat_url_from_provenance(token)
        if chat_url:
            break
    if not chat_url:
        for token in tokens:
            chat_url = await resolve_harvest_chat_url(
                HarvestRequest(execution_id=token),
                store,
            )
            if chat_url:
                break

    if not chat_url:
        return None

    satellite_id = next(
        (token for token in tokens if len(token) == 32 and "-" not in token),
        tokens[0],
    )
    stargate_id = next((token for token in tokens if "-" in token), None)
    if stargate_id is None:
        stargate_id = stargate_id_from_satellite(satellite_id)

    attempt_key = satellite_id
    if not _recovery_allowed(attempt_key):
        return None
    _mark_recovery_attempt(attempt_key)
    return await _harvest_chat_to_snapshot(
        chat_url=chat_url,
        satellite_id=satellite_id,
        stargate_execution_id=stargate_id,
    )
