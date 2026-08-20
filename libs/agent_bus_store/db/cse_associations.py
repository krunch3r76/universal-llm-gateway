"""Append-only CSE session-address association store helpers.

Participates in store-allocated ordering (SQLite AUTOINCREMENT ``id``) and
derived-current reads via ``MAX(id)`` per ``thread_id``. Append only — never
mutate a current-holder pointer. Identity is the Cowork session address
(``cse_chat_url``); ``cse_registration_id`` is last-known Chrome host bind.
"""

from __future__ import annotations

from typing import Any

from claude_bundles.cse_url import normalize_cse_url

from ..events.cse_bound import emit_cse_bound
from .connection import connect

_CSE_PATH_MARKER = "/cowork/cse_"


def _normalize_registration_id(raw: str | None) -> str | None:
    value = (raw or "").strip()
    return value or None


def normalize_cse_bind_url(raw: str | None) -> str | None:
    """Return a bindable Cowork URL, or None when the value is not a session address."""
    url = normalize_cse_url(raw or "")
    if not url or _CSE_PATH_MARKER not in url:
        return None
    return url


def _prior_row(conn, *, thread_id: str) -> Any | None:
    return conn.execute(
        "SELECT id, cse_chat_url, cse_registration_id "
        "FROM thread_cse_associations "
        "WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
        (thread_id,),
    ).fetchone()


def associate_cse(
    *,
    thread_id: str,
    cse_chat_url: str | None,
    cse_registration_id: str | None = None,
    bound_by: str | None = None,
    evidence: str | None = None,
) -> dict[str, Any] | None:
    """Append one CSE association when the URL is new or the attach host changed.

    Returns None when the URL is missing/invalid (registration-only is not a
    bind) or when the folded current pair already matches. Returns the insert
    echo when a row is appended.
    """
    from .threads import get_thread, normalize_thread_id

    thread_id = normalize_thread_id(thread_id)
    url = normalize_cse_bind_url(cse_chat_url)
    if url is None:
        return None
    registration_id = _normalize_registration_id(cse_registration_id)

    if get_thread(thread_id) is None:
        raise LookupError(f"Thread {thread_id} not found")

    with connect() as conn:
        prior = _prior_row(conn, thread_id=thread_id)
        if prior is not None:
            prior_url = str(prior["cse_chat_url"] or "")
            prior_reg = _normalize_registration_id(prior["cse_registration_id"])
            if prior_url == url and prior_reg == registration_id:
                return None
        prior_id = int(prior["id"]) if prior is not None else None
        cur = conn.execute(
            "INSERT INTO thread_cse_associations "
            "(thread_id, cse_chat_url, cse_registration_id, bound_by, evidence) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, url, registration_id, bound_by, evidence),
        )
        association_id = int(cur.lastrowid)

    emit_cse_bound(
        thread_id=thread_id,
        cse_chat_url=url,
        cse_registration_id=registration_id,
        association_id=association_id,
        prior_association_id=prior_id,
        bound_by=bound_by,
    )
    return {
        "thread_id": thread_id,
        "cse_chat_url": url,
        "cse_registration_id": registration_id,
        "id": association_id,
        "state": "associated",
    }


def get_current_cse(*, thread_id: str) -> dict[str, Any]:
    """Return derived current CSE bind for a thread from append-only history."""
    from .threads import get_thread, normalize_thread_id

    thread_id = normalize_thread_id(thread_id)
    if get_thread(thread_id) is None:
        raise LookupError(f"Thread {thread_id} not found")

    with connect() as conn:
        row = _prior_row(conn, thread_id=thread_id)

    if row is None:
        return {
            "thread_id": thread_id,
            "cse_chat_url": None,
            "cse_registration_id": None,
            "association_id": None,
            "state": "none",
        }
    return {
        "thread_id": thread_id,
        "cse_chat_url": row["cse_chat_url"],
        "cse_registration_id": row["cse_registration_id"],
        "association_id": int(row["id"]),
        "state": "associated",
    }


def merge_cse_fields(rows: list[dict[str, Any]]) -> None:
    """Fold the newest CSE association onto each thread row in place.

    Missing history leaves both ``cse_chat_url`` and ``cse_registration_id``
    as None so ``thread_get`` stays honest when a lane has never bound.
    """
    if not rows:
        return
    thread_ids = [str(row["id"]) for row in rows if row.get("id") is not None]
    if not thread_ids:
        thread_ids = [str(row["thread"]) for row in rows if row.get("thread")]
    if not thread_ids:
        return
    placeholders = ",".join("?" * len(thread_ids))
    sql = f"""
        SELECT tca.thread_id, tca.cse_chat_url, tca.cse_registration_id
        FROM thread_cse_associations tca
        INNER JOIN (
            SELECT thread_id, MAX(id) AS max_id
            FROM thread_cse_associations
            GROUP BY thread_id
        ) latest
            ON tca.thread_id = latest.thread_id AND tca.id = latest.max_id
        WHERE tca.thread_id IN ({placeholders})
    """
    with connect() as conn:
        cse_rows = conn.execute(sql, thread_ids).fetchall()
    cse_map = {
        row["thread_id"]: {
            "cse_chat_url": row["cse_chat_url"],
            "cse_registration_id": row["cse_registration_id"],
        }
        for row in cse_rows
    }
    for row in rows:
        key = str(row.get("id") or row.get("thread"))
        folded = cse_map.get(key)
        if folded is None:
            row["cse_chat_url"] = None
            row["cse_registration_id"] = None
        else:
            row["cse_chat_url"] = folded["cse_chat_url"]
            row["cse_registration_id"] = folded["cse_registration_id"]
