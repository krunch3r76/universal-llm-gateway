"""Append-only endpoint subscription journal."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any

from agent_seat.registry import normalize_bus_address
from cursor_bridge.wake_delivery import normalize_target

from .connection import connect, write_connect

_IDENTITY = ("state", "surface", "display", "title", "agent_id")
# Broadcast and non-Grok seats never take a grok ``*`` row: a cursor/grok
# wildcard makes every thread display-seated and silences CSE wakes.
_GROK_WILDCARD_RESERVED = frozenset(
    {
        "all",
        "team",
        "cursor",
        "maestro",
        "cursor-sdk",
        "dispatch",
        "conductor-hop",
        "git-integration-worker",
        "frontier-admit",
    }
)


class SubscribeError(Exception):
    """Refused subscribe. ``code`` is the HTTP detail code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def current_subscription(agent: str, thread_scope: str) -> dict[str, Any] | None:
    """Return the newest row for one (agent, thread_scope), if any."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM agent_endpoint_subscriptions "
            "WHERE agent=? AND thread_scope=? AND actuation IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (agent, thread_scope),
        ).fetchone()
    return _row(row)


def list_subscriptions(
    agent: str | None = None, thread_scope: str | None = None
) -> list[dict[str, Any]]:
    """List current rows (MAX id per key), optionally filtered."""
    clauses = [
        "actuation IS NULL",
        "id IN (SELECT MAX(id) FROM agent_endpoint_subscriptions "
        "WHERE actuation IS NULL GROUP BY agent, thread_scope)",
    ]
    params: list[str] = []
    if agent is not None:
        clauses.append("agent=?")
        params.append(agent)
    if thread_scope is not None:
        clauses.append("thread_scope=?")
        params.append(thread_scope)
    sql = (
        "SELECT * FROM agent_endpoint_subscriptions WHERE "
        + " AND ".join(clauses)
        + " ORDER BY id"
    )
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row(r) for r in rows if r is not None]


def resolve_endpoint(agent: str, thread_id: str) -> tuple[str, dict[str, Any]] | None:
    """Exact scope, parent-thread scope, then grok wildcard."""
    from .endpoint_wake_ledger import resolve_subscription_for_thread

    return resolve_subscription_for_thread(agent, thread_id)


def _same(current: dict[str, Any], nxt: dict[str, Any]) -> bool:
    return all(current.get(k) == nxt.get(k) for k in _IDENTITY)


def subscribe_endpoint(
    *,
    agent: str,
    thread_scope: str,
    surface: str,
    display: str,
    title: str | None = None,
    agent_id: str | None = None,
    bound_by: str | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    """Append a subscribed row, or return the current row when identical."""
    canon = normalize_bus_address(agent)
    if canon == "web-anthropic":
        raise SubscribeError("endpoint_cse_delegated")
    scope = thread_scope.strip()
    surface_raw = str(surface).strip().lower()
    wildcard_ok = (
        surface_raw == "grok" and bool(canon) and canon not in _GROK_WILDCARD_RESERVED
    ) or (surface_raw == "cse" and canon == "maestro")
    if scope == "*" and not wildcard_ok:
        raise SubscribeError("endpoint_wildcard_refused")
    if not (title or agent_id):
        raise SubscribeError("endpoint_title_or_agent_required")
    if surface_raw == "cse":
        if not agent_id:
            raise SubscribeError("endpoint_title_or_agent_required")
        surface_n = "cse"
        display_n = str(display or "cse").strip().lower() or "cse"
    else:
        target = normalize_target(display, surface)
        if not target.get("ok"):
            raise SubscribeError("endpoint_target_refused")
        surface_n = str(target["app"])
        display_n = str(target["display"])
    proposed = {
        "state": "subscribed",
        "surface": surface_n,
        "display": display_n,
        "title": title,
        "agent_id": agent_id,
    }
    with write_connect() as conn:
        if scope != "*":
            exists = conn.execute(
                "SELECT id FROM threads WHERE id=?", (scope,)
            ).fetchone()
            if exists is None:
                raise SubscribeError("endpoint_thread_missing")
        current = conn.execute(
            "SELECT * FROM agent_endpoint_subscriptions "
            "WHERE agent=? AND thread_scope=? AND actuation IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (canon, scope),
        ).fetchone()
        current_d = _row(current)
        if current_d is not None and _same(current_d, proposed):
            return {
                "appended": False,
                "row": current_d,
                "prior": None,
                "prior_subscription_id": None,
            }
        prior_subscribed = conn.execute(
            "SELECT * FROM agent_endpoint_subscriptions "
            "WHERE agent=? AND thread_scope=? AND actuation IS NULL "
            "AND state='subscribed' "
            "AND (agent_id IS NULL OR agent_id != ?) "
            "ORDER BY id DESC LIMIT 1",
            (canon, scope, agent_id),
        ).fetchone()
        prior_before_supersede = _row(prior_subscribed)
        prior_for_response: dict[str, Any] | None = None
        prior_subscription_id: int | None = None
        if (
            current_d is not None
            and current_d.get("state") == "subscribed"
            and (
                current_d.get("agent_id") != agent_id
                or current_d.get("surface") != surface_n
            )
        ):
            tomb_evidence = f"superseded_by:{agent_id}"
            conn.execute(
                "INSERT INTO agent_endpoint_subscriptions "
                "(agent, thread_scope, state, surface, display, title, agent_id, bound_by, evidence) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    canon,
                    scope,
                    "unsubscribed",
                    current_d["surface"],
                    current_d["display"],
                    current_d["title"],
                    current_d["agent_id"],
                    bound_by,
                    tomb_evidence,
                ),
            )
            if (
                prior_before_supersede is not None
                and prior_before_supersede.get("id") is not None
            ):
                prior_subscription_id = int(prior_before_supersede["id"])
            prior_for_response = _row(
                conn.execute(
                    "SELECT * FROM agent_endpoint_subscriptions "
                    "WHERE agent=? AND thread_scope=? ORDER BY id DESC LIMIT 1",
                    (canon, scope),
                ).fetchone()
            )
        cur = conn.execute(
            "INSERT INTO agent_endpoint_subscriptions "
            "(agent, thread_scope, state, surface, display, title, agent_id, bound_by, evidence) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                canon,
                scope,
                "subscribed",
                surface_n,
                display_n,
                title,
                agent_id,
                bound_by,
                evidence,
            ),
        )
        new_id = int(cur.lastrowid)
        row = _row(
            conn.execute(
                "SELECT * FROM agent_endpoint_subscriptions WHERE id=?", (new_id,)
            ).fetchone()
        )
    assert row is not None
    return {
        "appended": True,
        "row": row,
        "prior": prior_for_response,
        "prior_subscription_id": prior_subscription_id,
    }


def unsubscribe_endpoint(
    *,
    agent: str,
    thread_scope: str,
    bound_by: str | None = None,
    evidence: str | None = None,
    expected_agent_id: str | None = None,
) -> dict[str, Any]:
    """Append an unsubscribed tombstone, or no-op when already tombstoned.

    When ``expected_agent_id`` is set, tombstone only the row that still names
    that id. A subscription replaced before this write keeps the new row.
    """
    canon = normalize_bus_address(agent)
    scope = thread_scope.strip()
    expected = (expected_agent_id or "").strip() or None
    with write_connect() as conn:
        current = conn.execute(
            "SELECT * FROM agent_endpoint_subscriptions "
            "WHERE agent=? AND thread_scope=? AND actuation IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (canon, scope),
        ).fetchone()
        current_d = _row(current)
        if current_d is None or current_d["state"] == "unsubscribed":
            return {"appended": False, "row": current_d, "prior": current_d}
        if (
            expected is not None
            and str(current_d.get("agent_id") or "").strip() != expected
        ):
            return {"appended": False, "row": current_d, "prior": current_d}
        cur = conn.execute(
            "INSERT INTO agent_endpoint_subscriptions "
            "(agent, thread_scope, state, surface, display, title, agent_id, bound_by, evidence) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                canon,
                scope,
                "unsubscribed",
                current_d["surface"],
                current_d["display"],
                current_d["title"],
                current_d["agent_id"],
                bound_by,
                evidence,
            ),
        )
        new_id = int(cur.lastrowid)
        row = _row(
            conn.execute(
                "SELECT * FROM agent_endpoint_subscriptions WHERE id=?", (new_id,)
            ).fetchone()
        )
    assert row is not None
    return {"appended": True, "row": row, "prior": current_d}


MAIN_WAKE_AGENT = "main-wake"
MAIN_WAKE_SCOPE = "main-wake"
MAIN_TARGET_SHA256 = (
    "dc5bff63b080012527eb9ecd366940da30ea107eefeab2b71efd578e8408b633"
)
INSTANCE_TARGET_SHA256 = (
    "2d78994cba5b9a614fd7051e047f3ddaa5ffae822fd4200da03c3ccae0ecf4cc"
)
_SERVER_FILE_CAS = "server_file_cas"
_CHATGPT_WORK = "chatgpt_work"
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)


class ChatgptWorkCasError(Exception):
    """Refused chatgpt_work CAS write. ``code`` is the HTTP detail code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError as exc:
        raise ChatgptWorkCasError("target_refused") from exc


def _parse_target_lines(path: Path, required: frozenset[str]) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ChatgptWorkCasError("target_refused") from exc
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key:
            parsed[key] = value
    if not required.issubset(parsed.keys()):
        raise ChatgptWorkCasError("target_refused")
    return parsed


def _conversation_uuid(url: str) -> str | None:
    match = _UUID_RE.search(url.split("?", 1)[0])
    return match.group(0) if match else None


def main_wake_latest() -> dict[str, Any] | None:
    """Newest main-wake journal row (any state)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM agent_endpoint_subscriptions "
            "WHERE actuation=? AND agent=? AND scope_kind='agent' "
            "ORDER BY id DESC LIMIT 1",
            (_CHATGPT_WORK, MAIN_WAKE_AGENT),
        ).fetchone()
    return _row(row)


def instance_wake_latest(home_house_id: str) -> dict[str, Any] | None:
    """Newest instance journal row for a home house (any state)."""
    house = str(home_house_id or "").strip()
    if not house:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM agent_endpoint_subscriptions "
            "WHERE actuation=? AND scope_kind='agent' AND home_house_id=? "
            "AND agent <> ? "
            "ORDER BY id DESC LIMIT 1",
            (_CHATGPT_WORK, house, MAIN_WAKE_AGENT),
        ).fetchone()
    return _row(row)


def _rejected_url_exists(agent_key: str, url: str) -> bool:
    clean = url.split("?", 1)[0]
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM agent_endpoint_subscriptions "
            "WHERE actuation=? AND agent=? AND title=? "
            "AND evidence LIKE 'rejected:%' LIMIT 1",
            (_CHATGPT_WORK, agent_key, clean),
        ).fetchone()
    return row is not None


def _insert_work_row(
    conn: sqlite3.Connection,
    *,
    agent: str,
    thread_scope: str,
    home_house_id: str | None,
    state: str,
    title: str,
    agent_id: str,
    bound_by: str,
    evidence: str,
    predecessor_id: int | None,
) -> dict[str, Any]:
    cur = conn.execute(
        "INSERT INTO agent_endpoint_subscriptions "
        "(agent, thread_scope, state, surface, display, title, agent_id, "
        "bound_by, evidence, scope_kind, actuation, predecessor_id, home_house_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            agent,
            thread_scope,
            state,
            None,
            None,
            title.split("?", 1)[0],
            agent_id,
            bound_by,
            evidence,
            "agent",
            _CHATGPT_WORK,
            predecessor_id,
            home_house_id,
        ),
    )
    new_id = int(cur.lastrowid)
    row = _row(
        conn.execute(
            "SELECT * FROM agent_endpoint_subscriptions WHERE id=?", (new_id,)
        ).fetchone()
    )
    assert row is not None
    return row


def _cas_replace_head(
    conn: sqlite3.Connection,
    *,
    agent_key: str,
    thread_scope: str,
    home_house_id: str | None,
    expected_old_url: str,
    new_url: str,
    attempt_id: str,
    handoff_sha256: str,
) -> dict[str, Any]:
    if agent_key == MAIN_WAKE_AGENT:
        head = conn.execute(
            "SELECT * FROM agent_endpoint_subscriptions "
            "WHERE actuation=? AND agent=? AND scope_kind='agent' "
            "ORDER BY id DESC LIMIT 1",
            (_CHATGPT_WORK, agent_key),
        ).fetchone()
    else:
        head = conn.execute(
            "SELECT * FROM agent_endpoint_subscriptions "
            "WHERE actuation=? AND agent=? AND scope_kind='agent' "
            "AND home_house_id=? "
            "ORDER BY id DESC LIMIT 1",
            (_CHATGPT_WORK, agent_key, home_house_id),
        ).fetchone()
    head_d = _row(head)
    clean_new = new_url.split("?", 1)[0]
    conv_id = _conversation_uuid(clean_new)
    if not conv_id:
        raise ChatgptWorkCasError("work_target_refused")
    if _rejected_url_exists(agent_key, clean_new):
        raise ChatgptWorkCasError("superseded_target_rejected")

    from web_chat_relay.orion_wake import PINNED_CONVERSATION_URL

    pin_url = PINNED_CONVERSATION_URL.split("?", 1)[0]
    expected = expected_old_url.strip()
    if expected.upper() == "ABSENT":
        if head_d is not None and str(head_d.get("state") or "") == "subscribed":
            raise ChatgptWorkCasError("seat_cas_conflict")
    elif head_d is None:
        if agent_key == MAIN_WAKE_AGENT and expected != pin_url:
            raise ChatgptWorkCasError("seat_cas_conflict")
        elif agent_key != MAIN_WAKE_AGENT:
            raise ChatgptWorkCasError("seat_cas_conflict")
    else:
        head_title = str(head_d.get("title") or "").split("?", 1)[0]
        if head_title != expected.split("?", 1)[0]:
            raise ChatgptWorkCasError("seat_cas_conflict")
        if str(head_d.get("state") or "") != "subscribed":
            raise ChatgptWorkCasError("seat_cas_conflict")

    if head_d is not None and str(head_d.get("state") or "") == "subscribed":
        old_id = int(head_d["id"])
        conn.execute(
            "INSERT INTO agent_endpoint_subscriptions "
            "(agent, thread_scope, state, surface, display, title, agent_id, "
            "bound_by, evidence, scope_kind, actuation, predecessor_id, home_house_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                agent_key,
                thread_scope,
                "unsubscribed",
                None,
                None,
                head_d["title"],
                head_d["agent_id"],
                _SERVER_FILE_CAS,
                f"superseded_by:{old_id}",
                "agent",
                _CHATGPT_WORK,
                None,
                home_house_id,
            ),
        )
        predecessor_id: int | None = old_id
    else:
        predecessor_id = None

    evidence = f"attempt_id={attempt_id};handoff_sha256={handoff_sha256}"
    row = _insert_work_row(
        conn,
        agent=agent_key,
        thread_scope=thread_scope,
        home_house_id=home_house_id,
        state="subscribed",
        title=clean_new,
        agent_id=conv_id,
        bound_by=_SERVER_FILE_CAS,
        evidence=evidence,
        predecessor_id=predecessor_id,
    )
    return row


def cas_main_wake_head(target_path: Path) -> dict[str, Any]:
    """Hash-pinned CAS writer for the main Orion wake head (21207 journal)."""
    path = Path(target_path)
    if _file_sha256(path) != MAIN_TARGET_SHA256:
        raise ChatgptWorkCasError("target_refused")
    fields = _parse_target_lines(
        path,
        frozenset(
            {
                "expected_old_url",
                "new_url",
                "attempt_id",
                "handoff_sha256",
            }
        ),
    )
    with write_connect() as conn:
        row = _cas_replace_head(
            conn,
            agent_key=MAIN_WAKE_AGENT,
            thread_scope=MAIN_WAKE_SCOPE,
            home_house_id=None,
            expected_old_url=fields["expected_old_url"],
            new_url=fields["new_url"],
            attempt_id=fields["attempt_id"],
            handoff_sha256=fields["handoff_sha256"],
        )
    return {"appended": True, "row": row}


def cas_instance_wake_head(target_path: Path) -> dict[str, Any]:
    """Hash-pinned CAS writer for a satellite instance wake head."""
    path = Path(target_path)
    if _file_sha256(path) != INSTANCE_TARGET_SHA256:
        raise ChatgptWorkCasError("target_refused")
    fields = _parse_target_lines(
        path,
        frozenset(
            {
                "instance_id",
                "home_house_id",
                "expected_old_url",
                "new_url",
                "attempt_id",
                "handoff_sha256",
            }
        ),
    )
    instance_id = fields["instance_id"].strip()
    home_house_id = fields["home_house_id"].strip()
    if not instance_id or not home_house_id:
        raise ChatgptWorkCasError("target_refused")
    with write_connect() as conn:
        if fields["expected_old_url"].strip().upper() == "ABSENT":
            occupied = conn.execute(
                "SELECT 1 FROM agent_endpoint_subscriptions AS a "
                "WHERE a.actuation=? AND a.home_house_id=? AND a.agent <> ? "
                "AND a.state='subscribed' "
                "AND a.id = ("
                "  SELECT MAX(b.id) FROM agent_endpoint_subscriptions AS b "
                "  WHERE b.actuation=a.actuation AND b.home_house_id=a.home_house_id "
                "  AND b.agent=a.agent"
                ") LIMIT 1",
                (_CHATGPT_WORK, home_house_id, instance_id),
            ).fetchone()
            if occupied is not None:
                raise ChatgptWorkCasError("seat_cas_conflict")
        row = _cas_replace_head(
            conn,
            agent_key=instance_id,
            thread_scope=instance_id,
            home_house_id=home_house_id,
            expected_old_url=fields["expected_old_url"],
            new_url=fields["new_url"],
            attempt_id=fields["attempt_id"],
            handoff_sha256=fields["handoff_sha256"],
        )
    return {"appended": True, "row": row}


def insert_chatgpt_work_row_for_test(
    *,
    instance_id: str,
    home_house_id: str,
    conversation_url: str,
    state: str = "subscribed",
    predecessor_id: int | None = None,
) -> dict[str, Any]:
    """Test helper: direct insert of an instance-scope work row."""
    clean = conversation_url.split("?", 1)[0]
    conv_id = _conversation_uuid(clean)
    if not conv_id:
        raise ValueError("invalid conversation_url")
    with write_connect() as conn:
        return _insert_work_row(
            conn,
            agent=instance_id,
            thread_scope=instance_id,
            home_house_id=home_house_id,
            state=state,
            title=clean,
            agent_id=conv_id,
            bound_by="test",
            evidence="test",
            predecessor_id=predecessor_id,
        )


def insert_main_wake_row_for_test(
    *,
    conversation_url: str,
    state: str = "subscribed",
    predecessor_id: int | None = None,
) -> dict[str, Any]:
    """Test helper: direct insert of a main-wake journal row."""
    clean = conversation_url.split("?", 1)[0]
    conv_id = _conversation_uuid(clean)
    if not conv_id:
        raise ValueError("invalid conversation_url")
    with write_connect() as conn:
        return _insert_work_row(
            conn,
            agent=MAIN_WAKE_AGENT,
            thread_scope=MAIN_WAKE_SCOPE,
            home_house_id=None,
            state=state,
            title=clean,
            agent_id=conv_id,
            bound_by="test",
            evidence="test",
            predecessor_id=predecessor_id,
        )

