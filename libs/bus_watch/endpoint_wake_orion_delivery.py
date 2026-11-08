"""Orion chatgpt_work Stargate POST and subscription row shaping (extracted from dispatcher)."""

from __future__ import annotations

import json
from typing import Any

import httpx
from transport_utils import DEFAULT_STARGATE_URL, make_sync_client
from web_chat_relay.orion_wake import (
    DEFAULT_ORION_CDP_URL,
    conversation_id_from_url,
    holder_conversation_url,
)

from bus_watch.endpoint_wake_transport import classify_orion


def chatgpt_work_subscription_from_actuation(
    actuation: dict[str, Any],
    *,
    subscription_id: int | None = None,
) -> tuple[str, dict[str, Any]] | None:
    url = str(actuation.get("conversation_url") or "").split("?", 1)[0]
    conv_id = conversation_id_from_url(url) or str(
        actuation.get("conversation_id") or ""
    )
    if not url or not conv_id:
        return None
    sub: dict[str, Any] = {
        "surface": "chatgpt",
        "display": "12",
        "title": url,
        "agent_id": conv_id,
        "state": "subscribed",
    }
    if subscription_id is not None:
        sub["id"] = int(subscription_id)
    return ("exact", sub)


def orion_chat_wake_post(
    rows: list[dict[str, Any]],
    sub: dict[str, Any],
    turn: int | str,
    *,
    from_agent: str,
    wake_id: str | None,
) -> tuple[str, str]:
    row = rows[0]
    turn_ids = sorted({int(r["turn_id"]) for r in rows})
    turn_numbers = sorted({int(r["turn_number"]) for r in rows})
    sub_id_raw = sub.get("id")
    try:
        sub_id_int = int(sub_id_raw) if sub_id_raw is not None else None
    except (TypeError, ValueError):
        sub_id_int = None
    binding_ref = (
        f"agent_endpoint_subscriptions:{sub_id_int}"
        if sub_id_int is not None
        else None
    )
    options: dict[str, Any] = {
        "thread": row["thread_id"],
        "turn": turn,
        "from_agent": from_agent,
        "conversation_url": holder_conversation_url(sub),
        "cdp_url": DEFAULT_ORION_CDP_URL,
        "wake_id": wake_id,
        "agent": row.get("agent") or "orion",
        "turn_ids": turn_ids,
        "covered_turn_ids": turn_ids,
        "turn_numbers": turn_numbers,
        "binding_reference": binding_ref,
    }
    if sub_id_int is not None:
        options["subscription_id"] = sub_id_int
    body = {
        "model": "orion-chat-wake",
        "messages": [{"role": "user", "content": "wake"}],
        "pipeline_options": options,
    }
    try:
        with make_sync_client(DEFAULT_STARGATE_URL, timeout=45.0) as client:
            last_status = 0
            parsed: Any = None
            response = client.post(
                "/v1/chat/completions",
                json=body,
                headers={"X-ULG-Surface": "code"},
            )
            last_status = int(response.status_code)
            try:
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
                parsed = json.loads(content) if isinstance(content, str) else content
            except (
                KeyError,
                IndexError,
                TypeError,
                json.JSONDecodeError,
                ValueError,
            ):
                parsed = None
            if last_status < 400 and isinstance(parsed, dict):
                return classify_orion(parsed)
            if last_status >= 500 and last_status < 600:
                response2 = client.post(
                    "/v1/chat/completions",
                    json=body,
                    headers={"X-ULG-Surface": "code"},
                )
                last_status = int(response2.status_code)
                try:
                    payload2 = response2.json()
                    content2 = payload2["choices"][0]["message"]["content"]
                    parsed = (
                        json.loads(content2)
                        if isinstance(content2, str)
                        else content2
                    )
                except (
                    KeyError,
                    IndexError,
                    TypeError,
                    json.JSONDecodeError,
                    ValueError,
                ):
                    parsed = None
                if last_status < 400 and isinstance(parsed, dict):
                    return classify_orion(parsed)
            return classify_orion(
                parsed if isinstance(parsed, dict) else None,
                http_status=last_status,
            )
    except httpx.ConnectError:
        return "pending", "connect"
    except Exception as exc:  # noqa: BLE001
        return "unknown", type(exc).__name__


__all__ = [
    "chatgpt_work_subscription_from_actuation",
    "orion_chat_wake_post",
]
