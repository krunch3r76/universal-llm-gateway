"""Cowork adapter — cse_session harvest / followup relays."""

from __future__ import annotations

import os
from typing import Any

import httpx

HTTP_TIMEOUT_SLACK_S = 60.0


def _project_ask_url() -> str:
    return os.environ.get("PROJECT_ASK_URL", "").strip()


def _http_timeout(satellite_timeout_s: float) -> float:
    return float(satellite_timeout_s) + HTTP_TIMEOUT_SLACK_S


def _unconfigured() -> dict[str, Any]:
    return {
        "ok": False,
        "code": "cse_session_unconfigured",
        "reason": "PROJECT_ASK_URL not configured for Cowork relay",
    }


def _relay(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    base = _project_ask_url()
    if not base:
        return _unconfigured()
    url = f"{base.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=_http_timeout(timeout_s)) as client:
            resp = client.request(method, url, json=json_body, params=params)
            if resp.status_code in {403, 404, 409, 424} and resp.content:
                body = resp.json()
                code = str(body.get("code") or "protocol_error")
                return {
                    "ok": False,
                    "code": code,
                    "reason": str(body.get("message") or body.get("error") or code),
                    **{k: v for k, v in body.items() if k not in {"code", "message"}},
                }
            resp.raise_for_status()
            if resp.content:
                return resp.json()
            return {"ok": True}
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "code": "cse_session_unreachable",
            "reason": f"cse-session relay failed: {exc}",
        }


def resolve_maestro_attended(*, parent_thread: str) -> dict[str, Any]:
    return _relay(
        "GET",
        "/v1/project-ask/attended-operator",
        params={"parent_thread": parent_thread},
        timeout_s=30.0,
    )


def harvest(
    *,
    after_turn: int | None,
    limit: int = 10,
    chat_url: str | None = None,
    registration_id: str | None = None,
    parent_thread: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"limit": limit}
    if after_turn is not None:
        body["after_turn"] = after_turn
    if chat_url:
        body["chat_url"] = chat_url
    if registration_id:
        body["registration_id"] = registration_id
    if parent_thread and not chat_url and not registration_id:
        attended = resolve_maestro_attended(parent_thread=parent_thread)
        if not attended.get("ok", True) and attended.get("code"):
            return attended
        chat_url = str(attended.get("chat_url") or "").strip()
        registration_id = str(attended.get("registration_id") or "").strip()
        if chat_url:
            body["chat_url"] = chat_url
        if registration_id:
            body["registration_id"] = registration_id
    return _relay("POST", "/v1/cse-session/harvest", json_body=body)


def followup(
    *,
    prompt_text: str,
    chat_url: str | None = None,
    registration_id: str | None = None,
    parent_thread: str | None = None,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    body: dict[str, Any] = {"prompt_text": prompt_text, "purpose": "ask"}
    if chat_url:
        body["chat_url"] = chat_url
    if registration_id:
        body["registration_id"] = registration_id
    if parent_thread:
        body["parent_thread"] = parent_thread
    return _relay(
        "POST",
        "/v1/project-ask/followups",
        json_body=body,
        timeout_s=timeout_s,
    )


__all__ = ["followup", "harvest", "resolve_maestro_attended"]
