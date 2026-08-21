"""MCP cse_session tool — thin httpx relay to the Jupiter CDP-ask satellite.

Ops: provenance/harvest (read), paste (hop-pair grant), followup (warm CSE
wake), resolve_attended (read). paste ≠ followup — different satellite routes.
No claude_bundles or cdp_ask imports — relay only per [universal:mcp].
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Literal

import httpx
from mcp_events import record

from tools import cse_session_warm
from tools.cse_session_warm import http_client_timeout_s, transport_failure_payload

if TYPE_CHECKING:
    from fastmcp import FastMCP


def _project_ask_url() -> str:
    return os.environ.get("PROJECT_ASK_URL", "").strip()


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
        return {
            "error": (
                "PROJECT_ASK_URL not configured. Start the cdp-ask satellite on "
                "Jupiter and set PROJECT_ASK_URL=http://HOST:PORT in the MCP "
                "server environment."
            )
        }
    url = f"{base.rstrip('/')}{path}"
    http_timeout = http_client_timeout_s(timeout_s)
    try:
        with httpx.Client(timeout=http_timeout) as client:
            resp = client.request(method, url, json=json_body, params=params)
            if resp.status_code in {403, 404, 409, 424}:
                if resp.content:
                    body = resp.json()
                    code = str(body.get("code") or body.get("detail") or "protocol_error")
                    return {
                        "code": code,
                        "message": str(body.get("message") or code),
                        "source": "gateway",
                        "retryable": bool(body.get("retryable", False)),
                        "data": {k: v for k, v in body.items() if k not in {"code", "message"}},
                        "ok": False,
                    }
            resp.raise_for_status()
            if resp.content:
                return resp.json()
            return {"ok": True}
    except httpx.HTTPStatusError as exc:
        record(
            "mcp.cse_session.relay.failed",
            path=path,
            kind="http_status",
            status=exc.response.status_code,
        )
        detail = exc.response.text[:400]
        return {
            "error": f"cse-session HTTP {exc.response.status_code}",
            "status_code": exc.response.status_code,
            "detail": detail,
        }
    except httpx.RequestError as exc:
        return transport_failure_payload(exc, path=path, timeout_s=http_timeout)


def register_cse_session_tool(mcp: FastMCP) -> None:
    """Register the cse_session relay on both /mcp/life and /mcp/code."""

    @mcp.tool(title="CSE Session")
    def cse_session(
        op: Literal[
            "provenance", "harvest", "paste", "followup", "resolve_attended"
        ],
        chat_url: str | None = None,
        registration_id: str | None = None,
        execution_id: str | None = None,
        predecessor_registration_id: str | None = None,
        successor_registration_id: str | None = None,
        limit: int = 10,
        after_turn: int | None = None,
        source: Literal["chat", "output-file", "auto"] = "auto",
        metadata_only: bool = False,
        marker: str | None = None,
        successor_birth_id: str | None = None,
        prompt_text: str | None = None,
        prompt_uri: str | None = None,
        prompt_path: str | None = None,
        cdp_url: str | None = None,
        purpose: str = "ask",
        timeout_s: int = 60,
        reattach: bool = False,
        retain_lane: bool = False,
        envelope: Literal["free", "stand_down", "page"] = "free",
        grant: str | None = None,
        caller_registration_id: str | None = None,
        parent_thread: str | None = None,
        superseded_registration_id: str | None = None,
        idempotency_key: str | None = None,
        min_receipt: Literal["dom_paste", "dom_committed", "human_visible"] = "dom_paste",
    ) -> dict[str, Any]:
        """CSE provenance, harvest, hop-pair paste, warm followup, attended resolve.

        ``paste`` = hop-pair / grant authorized paste (``/v1/cse-session/paste``).
        ``followup`` = warm wake into a retained or dormant Cowork CSE
        (``/v1/project-ask/followups``). Identity omitted on followup ⇒ attended
        resolve. New CDP consults use ``team_dispatch(model=cdp/…)``. IF6 submit
        is CLI. See agent_skill:claude-ai-cdp-navigation.
        """
        if op == "provenance":
            params = {
                k: v
                for k, v in {
                    "chat_url": chat_url,
                    "registration_id": registration_id,
                    "execution_id": execution_id,
                    "predecessor_registration_id": predecessor_registration_id,
                    "successor_registration_id": successor_registration_id,
                }.items()
                if v
            }
            result = _relay("GET", "/v1/cse-session/provenance", params=params)
            record(
                "mcp.cse_session.provenance",
                state=result.get("state"),
                registration_id=result.get("registration_id"),
            )
            return result

        if op == "harvest":
            body = {
                k: v
                for k, v in {
                    "chat_url": chat_url,
                    "registration_id": registration_id,
                    "execution_id": execution_id,
                    "limit": limit,
                    "after_turn": after_turn,
                    "source": source,
                    "metadata_only": metadata_only,
                    "marker": marker,
                    "successor_birth_id": successor_birth_id,
                }.items()
                if v is not None and v != ""
            }
            result = _relay("POST", "/v1/cse-session/harvest", json_body=body)
            record(
                "mcp.cse_session.harvest",
                outcome=result.get("outcome"),
                ack_class=result.get("ack_class"),
            )
            return result

        if op == "resolve_attended":
            return cse_session_warm.relay_attended()

        if op == "followup":
            return cse_session_warm.relay_followup(
                chat_url=chat_url,
                registration_id=registration_id,
                execution_id=execution_id,
                cdp_url=cdp_url,
                prompt_text=prompt_text,
                prompt_uri=prompt_uri,
                prompt_path=prompt_path,
                purpose=purpose,
                timeout_s=float(timeout_s),
                reattach=reattach,
                retain_lane=retain_lane,
                min_receipt=min_receipt,
            )

        if not chat_url and not registration_id:
            return {
                "ok": False,
                "code": "identity_required",
                "error": "paste requires chat_url or registration_id",
            }
        body = {
            k: v
            for k, v in {
                "chat_url": chat_url,
                "registration_id": registration_id,
                "prompt_text": prompt_text,
                "prompt_uri": prompt_uri,
                "envelope": envelope,
                "grant": grant,
                "caller_registration_id": caller_registration_id,
                "parent_thread": parent_thread,
                "superseded_registration_id": superseded_registration_id,
                "idempotency_key": idempotency_key,
                "min_receipt": min_receipt if min_receipt != "dom_paste" else None,
            }.items()
            if v is not None and v != ""
        }
        result = _relay("POST", "/v1/cse-session/paste", json_body=body)
        record(
            "mcp.cse_session.paste",
            ok=result.get("ok"),
            receipt=result.get("receipt"),
            replayed=result.get("replayed"),
        )
        return result
