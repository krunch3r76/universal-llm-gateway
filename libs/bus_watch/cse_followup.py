"""CSE follow-up — paste liaison induction via cdp_ask ``/v1/project-ask/followups``."""

from __future__ import annotations

import os
from typing import Any, Protocol

import httpx
from cdp_ask.client import format_cdp_ask_http_error, project_ask_base_url
from claude_bundles.cdp_registry import chat_url_for_registration, list_active
from claude_bundles.cdp_registry_store import load_active
from claude_bundles.what_is_running_view import OPERATOR_PURPOSES

_FOLLOWUPS_PATH = "/v1/project-ask/followups"
_DEFAULT_TIMEOUT_S = 60.0
_HTTP_TIMEOUT_SLACK_S = 60.0


class HttpPoster(Protocol):
    """Test-injected POST callable for project-ask followups."""

    def __call__(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float,
    ) -> httpx.Response: ...


def resolve_cse_identity(root_id: str) -> dict[str, str | None]:
    """Resolve CSE ``chat_url`` / ``registration_id`` from registry ``parent_thread``."""
    parent = str(root_id or "").strip()
    if not parent:
        return {"chat_url": None, "registration_id": None, "url": None}

    active = load_active()
    rows: list[tuple[str, str | None, float]] = []
    for reg in list_active():
        purpose = (reg.purpose or "").strip()
        if purpose not in OPERATOR_PURPOSES:
            continue
        if str(reg.parent_thread or "").strip() != parent:
            continue
        raw = active.get(reg.registration_id) or {}
        started = raw.get("started_at")
        try:
            started_f = float(started) if started is not None else 0.0
        except (TypeError, ValueError):
            started_f = 0.0
        kind = str(reg.mission_kind or "root").strip().lower() or "root"
        rows.append((reg.registration_id, kind, started_f))

    if not rows:
        return {"chat_url": None, "registration_id": None, "url": None}

    hop_rows = [row for row in rows if row[1] == "hop"]
    reg_id = hop_rows[0][0] if len(hop_rows) == 1 else max(rows, key=lambda r: r[2])[0]
    url = (chat_url_for_registration(reg_id) or "").strip() or None
    return {"chat_url": url, "registration_id": reg_id, "url": url}


def fire_cse_followup(
    induction: str,
    root_id: str,
    *,
    dry_run: bool = False,
    post: HttpPoster | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """POST induction to the liaison CSE on ``parent_thread=root_id``; never raises."""
    root = str(root_id or "").strip()
    prompt = (induction or "").strip()
    if not prompt:
        return {"ok": False, "error": "no_prompt", "root": root}

    identity = resolve_cse_identity(root)
    chat_url = identity.get("chat_url")
    registration_id = identity.get("registration_id")
    url = identity.get("url")
    if not chat_url and not registration_id:
        return {
            "ok": False,
            "error": "no_identity",
            "root": root,
            "url": url,
            "registration_id": registration_id,
            "send_verified": False,
        }

    base = project_ask_base_url() or os.environ.get("PROJECT_ASK_URL", "").strip()
    if not base:
        return {
            "ok": False,
            "error": "project_ask_unconfigured",
            "root": root,
            "url": url,
            "registration_id": registration_id,
            "send_verified": False,
        }

    body: dict[str, Any] = {
        "prompt_text": prompt,
        "purpose": "operator-proxy",
        "timeout_s": int(timeout_s),
        "reattach": True,
    }
    if chat_url:
        body["chat_url"] = chat_url
    if registration_id:
        body["registration_id"] = registration_id

    endpoint = f"{base.rstrip('/')}{_FOLLOWUPS_PATH}"
    result: dict[str, Any] = {
        "root": root,
        "url": url,
        "registration_id": registration_id,
        "endpoint": endpoint,
    }
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "send_verified": False,
            "body": body,
            **result,
        }

    http_timeout = float(timeout_s) + _HTTP_TIMEOUT_SLACK_S
    try:
        if post is not None:
            resp = post("POST", endpoint, json=body, timeout=http_timeout)
        else:
            with httpx.Client(timeout=http_timeout) as client:
                resp = client.post(endpoint, json=body)
    except httpx.TimeoutException as exc:
        return {
            "ok": False,
            "error": f"cdp-ask timed out after {http_timeout:.0f}s: {exc}",
            "send_verified": False,
            **result,
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "error": f"cdp-ask unreachable: {exc}",
            "send_verified": False,
            **result,
        }

    if resp.status_code >= 400:
        detail = (resp.text or "")[:400]
        return {
            "ok": False,
            "error": format_cdp_ask_http_error(resp.status_code, detail),
            "status_code": resp.status_code,
            "send_verified": False,
            **result,
        }

    data = resp.json() if resp.content else {"ok": True}
    if not isinstance(data, dict):
        return {"ok": True, "send_verified": False, **result}

    send_verified = bool(data.get("send_verified"))
    response_url = data.get("url") or url
    response_reg = data.get("registration_id") or registration_id
    ok = bool(data.get("ok", True))
    return {
        "ok": ok,
        "url": response_url,
        "registration_id": response_reg,
        "send_verified": send_verified,
        **{k: v for k, v in data.items() if k not in result},
        **result,
    }


__all__ = ["fire_cse_followup", "resolve_cse_identity"]
