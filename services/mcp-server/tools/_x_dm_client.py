"""X (Twitter) Direct Message client for @KJMXYXX — OAuth 1.0a user context."""

from __future__ import annotations

import os
from typing import Any

import requests
from requests_oauthlib import OAuth1

_API_BASE = "https://api.x.com/2"
_DEFAULT_FIELDS = (
    "id,text,dm_conversation_id,created_at,sender_id,participant_ids,event_type"
)


def _missing_credentials() -> list[str]:
    required = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
    return [name for name in required if not os.getenv(name, "").strip()]


def _oauth() -> OAuth1:
    return OAuth1(
        os.environ["X_API_KEY"],
        os.environ["X_API_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_SECRET"],
    )


def _get(path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    resp = requests.get(
        f"{_API_BASE}{path}",
        auth=_oauth(),
        params=params or {},
        timeout=30,
    )
    if resp.status_code != 200:
        try:
            err = resp.json()
            detail = err.get("detail") or err.get("title") or resp.text[:200]
        except Exception:
            detail = resp.text[:200]
        raise RuntimeError(f"X API HTTP {resp.status_code}: {detail}")
    return resp.json()


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    resp = requests.post(
        f"{_API_BASE}{path}",
        auth=_oauth(),
        json=payload,
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        try:
            err = resp.json()
            detail = err.get("detail") or err.get("title") or resp.text[:200]
        except Exception:
            detail = resp.text[:200]
        raise RuntimeError(f"X API HTTP {resp.status_code}: {detail}")
    return resp.json()


def _users_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {u["id"]: u for u in (payload.get("includes") or {}).get("users", [])}


def _format_message(
    event: dict[str, Any], users: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    sender_id = event.get("sender_id")
    user = users.get(sender_id or "", {})
    return {
        "id": event.get("id"),
        "conversation_id": event.get("dm_conversation_id"),
        "created_at": event.get("created_at"),
        "sender_id": sender_id,
        "sender_username": user.get("username"),
        "sender_name": user.get("name"),
        "text": event.get("text") or "",
    }


def list_conversations(*, max_pages: int = 5) -> dict[str, Any]:
    """Scan recent DM events and summarize distinct conversations."""
    missing = _missing_credentials()
    if missing:
        return {"error": f"Missing env: {', '.join(missing)}"}

    pagination_token: str | None = None
    by_conv: dict[str, dict[str, Any]] = {}
    pages = 0

    while pages < max(1, min(max_pages, 10)):
        params: dict[str, Any] = {
            "max_results": 100,
            "event_types": "MessageCreate",
            "dm_event.fields": _DEFAULT_FIELDS,
            "expansions": "sender_id,participant_ids",
            "user.fields": "username,name",
        }
        if pagination_token:
            params["pagination_token"] = pagination_token

        data = _get("/dm_events", params=params)
        users = _users_by_id(data)
        for event in data.get("data") or []:
            cid = event.get("dm_conversation_id")
            if not cid:
                continue
            bucket = by_conv.setdefault(
                cid,
                {
                    "conversation_id": cid,
                    "message_count": 0,
                    "latest_at": "",
                    "latest_preview": "",
                    "participants": {},
                },
            )
            bucket["message_count"] += 1
            created = event.get("created_at") or ""
            if created >= bucket["latest_at"]:
                bucket["latest_at"] = created
                bucket["latest_preview"] = (event.get("text") or "")[:160]
            for pid in event.get("participant_ids") or []:
                u = users.get(pid, {})
                bucket["participants"][pid] = u.get("username") or pid
            sid = event.get("sender_id")
            if sid:
                u = users.get(sid, {})
                bucket["participants"][sid] = u.get("username") or sid

        pages += 1
        pagination_token = (data.get("meta") or {}).get("next_token")
        if not pagination_token:
            break

    conversations = []
    for bucket in sorted(
        by_conv.values(),
        key=lambda item: item.get("latest_at") or "",
        reverse=True,
    ):
        conversations.append(
            {
                "conversation_id": bucket["conversation_id"],
                "message_count": bucket["message_count"],
                "latest_at": bucket["latest_at"],
                "latest_preview": bucket["latest_preview"],
                "participants": [
                    {"id": pid, "username": name}
                    for pid, name in sorted(bucket["participants"].items())
                ],
            }
        )

    return {
        "account": "KJMXYXX",
        "conversations": conversations,
        "pages_scanned": pages,
        "_note": (
            "Only legacy DMs visible via API (~30 days). Encrypted X Chat "
            "(x.com/i/chat) does not appear here."
        ),
    }


def fetch_messages(
    *,
    conversation_id: str | None = None,
    limit: int = 50,
    since_id: str | None = None,
    pagination_token: str | None = None,
) -> dict[str, Any]:
    """Fetch DM messages globally or for one conversation."""
    missing = _missing_credentials()
    if missing:
        return {"error": f"Missing env: {', '.join(missing)}"}

    limit = max(1, min(limit, 100))
    params: dict[str, Any] = {
        "max_results": limit,
        "event_types": "MessageCreate",
        "dm_event.fields": _DEFAULT_FIELDS,
        "expansions": "sender_id,participant_ids",
        "user.fields": "username,name",
    }
    if since_id:
        params["since_id"] = since_id
    if pagination_token:
        params["pagination_token"] = pagination_token

    path = (
        f"/dm_conversations/{conversation_id}/dm_events"
        if conversation_id
        else "/dm_events"
    )
    data = _get(path, params=params)
    users = _users_by_id(data)
    messages = [_format_message(event, users) for event in data.get("data") or []]
    meta = data.get("meta") or {}

    return {
        "account": "KJMXYXX",
        "conversation_id": conversation_id,
        "messages": messages,
        "count": len(messages),
        "next_token": meta.get("next_token"),
        "result_count": meta.get("result_count"),
        "_note": (
            "Ephemeral fetch — not persisted. Encrypted X Chat is not available "
            "via API."
        ),
    }


def send_message(*, conversation_id: str, text: str) -> dict[str, Any]:
    """Post a message into an existing DM or group conversation."""
    missing = _missing_credentials()
    if missing:
        return {"error": f"Missing env: {', '.join(missing)}"}

    text = text.strip()
    if not text:
        return {"error": "text must be non-empty"}
    if len(text) > 280:
        return {"error": f"text too long ({len(text)} chars; max 280)"}

    data = _post(
        f"/dm_conversations/{conversation_id}/messages",
        {"text": text},
    )
    tweet = data.get("data") or {}
    return {
        "account": "KJMXYXX",
        "message_id": tweet.get("id"),
        "conversation_id": conversation_id,
        "text": text,
        "url": f"https://x.com/KJMXYXX/status/{tweet.get('id')}"
        if tweet.get("id")
        else None,
    }
