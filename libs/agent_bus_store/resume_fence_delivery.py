"""Stored resume bundles, adopt reuse, and single-flight pour delivery.

After FIX-13 pour-terminal release the stored row is the delivery artifact for
GET. The in-process single-flight map collapses concurrent pours per key; it is
not a cross-process lock.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any

from deploy_identity.code_version import resolve_code_version

from .db.connection import connect
from .house_pools import load_continuity_card
from .resume_fence import (
    _sha256_text,
    _tip_checkpoint,
    assemble_resume_fence,
    encode_resume_bundle,
)
from .resume_fence_store import _within_adopt_window, fold_fence

PourKey = tuple[str, str | None]

_IN_FLIGHT: dict[PourKey, asyncio.Task[dict[str, Any]]] = {}
_IN_FLIGHT_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class StoredBundle:
    fence_id: str
    sha256: str
    body_json: str
    tip_turn_id: int
    card_sha256: str | None
    head_sha: str | None
    built_at: str


def pour_key(thread_id: str, transcript_id: str | None) -> PourKey:
    """Normalize pour key: empty transcript id folds to thread-alone."""
    tid = (transcript_id or "").strip() or None
    return (thread_id, tid)


def read_stored_bundle(
    thread_id: str, transcript_id: str | None
) -> StoredBundle | None:
    """Latest stored bundle for root_thread + transcript_id (includes released)."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT b.fence_id, b.sha256, b.body_json, b.tip_turn_id,
                   b.card_sha256, b.head_sha, b.built_at
            FROM resume_fence_bundles b
            WHERE b.fence_id IN (
                SELECT fence_id FROM resume_fence_events
                WHERE root_thread = ? AND transcript_id IS ? AND event = 'poured'
            )
            ORDER BY b.built_at DESC
            LIMIT 1
            """,
            (thread_id, transcript_id),
        ).fetchone()
    if row is None:
        return None
    return StoredBundle(
        fence_id=str(row[0]),
        sha256=str(row[1]),
        body_json=str(row[2]),
        tip_turn_id=int(row[3]),
        card_sha256=row[4],
        head_sha=row[5],
        built_at=str(row[6]),
    )


def store_bundle(bundle: dict[str, Any]) -> StoredBundle:
    """Persist canonical pour-time bundle bytes; PK collision raises."""
    body = encode_resume_bundle(bundle)
    digest = hashlib.sha256(body).hexdigest()
    fence_id = bundle["fence"]["fence_id"]
    tip_turn_id = int(bundle["tip_checkpoint"]["turn_id"])
    card_sha256 = bundle["card"]["sha256"]
    head_sha = bundle["fence"]["head_sha"]
    built_at = bundle["provenance"]["built_at"]
    body_text = body.decode("utf-8")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO resume_fence_bundles
                (fence_id, sha256, body_json, tip_turn_id, card_sha256, head_sha, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fence_id,
                digest,
                body_text,
                tip_turn_id,
                card_sha256,
                head_sha,
                built_at,
            ),
        )
    return StoredBundle(
        fence_id=fence_id,
        sha256=digest,
        body_json=body_text,
        tip_turn_id=tip_turn_id,
        card_sha256=card_sha256,
        head_sha=head_sha,
        built_at=built_at,
    )


def _bundle_is_fresh(row: StoredBundle, thread_id: str) -> bool:
    if not _within_adopt_window(row.built_at):
        return False
    tip = _tip_checkpoint(thread_id)
    if tip is None or int(tip["id"]) != row.tip_turn_id:
        return False
    card = load_continuity_card(thread_id)
    card_sha = _sha256_text(card) if card else None
    if card_sha != row.card_sha256:
        return False
    return resolve_code_version() == row.head_sha


def _stamp_live_state(bundle: dict[str, Any], fence_id: str) -> dict[str, Any]:
    folded = fold_fence(fence_id)
    if folded is None:
        return bundle
    return {
        **bundle,
        "fence": {**bundle["fence"], "state": folded.state},
    }


def _pour_or_adopt(
    thread_id: str,
    *,
    transcript_id: str | None,
    source: str,
    pool: str | None,
) -> dict[str, Any]:
    row = read_stored_bundle(thread_id, transcript_id)
    if row is not None and _bundle_is_fresh(row, thread_id):
        stored = json.loads(row.body_json)
        return _stamp_live_state(stored, row.fence_id)
    bundle = assemble_resume_fence(
        thread_id,
        transcript_id=transcript_id,
        source=source,
        pool=pool,
    )
    if bundle.get("error"):
        return bundle
    store_bundle(bundle)
    fid = bundle["fence"]["fence_id"]
    return _stamp_live_state(bundle, fid)


def _drop_in_flight(key: PourKey, task: asyncio.Task[dict[str, Any]]) -> None:
    with _IN_FLIGHT_LOCK:
        if _IN_FLIGHT.get(key) is task:
            _IN_FLIGHT.pop(key, None)


async def deliver_resume_bundle(
    thread_id: str,
    *,
    transcript_id: str | None,
    source: str,
    pool: str | None,
) -> dict[str, Any]:
    """Single-flight pour or adopt for one root thread + transcript key."""
    key = pour_key(thread_id, transcript_id)
    with _IN_FLIGHT_LOCK:
        task = _IN_FLIGHT.get(key)
        if task is None:
            task = asyncio.ensure_future(
                asyncio.to_thread(
                    _pour_or_adopt,
                    thread_id,
                    transcript_id=key[1],
                    source=source,
                    pool=pool,
                )
            )
            _IN_FLIGHT[key] = task
            task.add_done_callback(lambda t, k=key: _drop_in_flight(k, t))
    return await asyncio.shield(task)


def pour_in_flight(key: PourKey) -> bool:
    with _IN_FLIGHT_LOCK:
        return key in _IN_FLIGHT


def read_delivery_view(
    thread_id: str, transcript_id: str | None
) -> tuple[StoredBundle, str] | None:
    row = read_stored_bundle(thread_id, transcript_id)
    if row is None:
        return None
    folded = fold_fence(row.fence_id)
    state = folded.state if folded is not None else "poured"
    return row, state
