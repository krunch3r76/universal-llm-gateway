"""HTTP client for the CDP project-ask satellite (native CDP executor).

Stargate ``/api/v1/providers/cdp/*`` and ``claude_bundles.cdp_model_endpoint``
share this client so submit/poll/abort params stay one contract
(``SubmitProjectAskRequest``).

Transport: ``transport_utils.make_sync_client`` / ``make_async_client`` (HTTP
or UDS). Auth: satellite is loopback-trusted today (no Bearer) — contrast
agent-bus which requires ``AGENT_BUS_TOKEN``; document in substrate-apis spec.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from transport_utils import make_async_client, make_sync_client

from .models import SubmitProjectAskRequest


def project_ask_base_url() -> str:
    """Return ``PROJECT_ASK_URL`` (satellite base) or empty string."""
    return os.environ.get("PROJECT_ASK_URL", "").strip().rstrip("/")


def format_cdp_ask_http_error(status_code: int, detail: str | None = None) -> str:
    """Caller-visible satellite HTTP error. MCP ``project_ask`` is gone."""
    text = (detail or "").strip()
    if text:
        return f"cdp-ask HTTP {status_code}: {text[:400]}"
    return f"cdp-ask HTTP {status_code}"


class CdpAskClientError(RuntimeError):
    """Satellite unreachable or returned a non-success HTTP status."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class CdpAskClient:
    """Thin sync/async relay to Jupiter project-ask satellite routes."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.base_url = (
            base_url if base_url is not None else project_ask_base_url()
        ).rstrip("/")
        self.timeout_s = timeout_s

    def _require_base(self) -> str:
        if not self.base_url:
            raise CdpAskClientError(
                "PROJECT_ASK_URL not configured. Start the cdp-ask satellite "
                "and set PROJECT_ASK_URL=http://HOST:PORT."
            )
        return self.base_url

    def submit(
        self,
        body: SubmitProjectAskRequest | dict[str, Any],
        *,
        client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        """POST ``/v1/project-ask/executions``."""
        payload = (
            body.model_dump(exclude_none=True)
            if isinstance(body, SubmitProjectAskRequest)
            else body
        )
        return self._request(
            "POST",
            "/v1/project-ask/executions",
            json_body=payload,
            client=client,
        )

    def poll(
        self,
        execution_id: str,
        *,
        client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        """GET ``/v1/project-ask/executions/{id}``."""
        return self._request(
            "GET",
            f"/v1/project-ask/executions/{execution_id}",
            client=client,
        )

    def abort(
        self,
        execution_id: str,
        *,
        client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        """POST ``/v1/project-ask/executions/{id}/abort``."""
        return self._request(
            "POST",
            f"/v1/project-ask/executions/{execution_id}/abort",
            client=client,
        )

    def paste(
        self,
        body: dict[str, Any],
        *,
        client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        """POST ``/v1/cse-session/paste``."""
        return self._request(
            "POST",
            "/v1/cse-session/paste",
            json_body=body,
            client=client,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        client: httpx.Client | None = None,
    ) -> dict[str, Any]:
        base = self._require_base()
        owns = client is None
        http = client or make_sync_client(base, timeout=self.timeout_s)
        try:
            resp = http.request(method, path, json=json_body)
            if resp.status_code >= 400:
                detail = resp.text[:400]
                raise CdpAskClientError(
                    format_cdp_ask_http_error(resp.status_code, detail),
                    status_code=resp.status_code,
                    detail=detail,
                )
            if resp.content:
                return resp.json()
            return {"ok": True}
        except httpx.RequestError as exc:
            raise CdpAskClientError(f"cdp-ask unreachable: {exc}") from exc
        finally:
            if owns:
                http.close()


async def relay_async(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    base_url: str | None = None,
    timeout_s: float = 60.0,
) -> tuple[int, dict[str, Any] | bytes, str]:
    """Async relay for Stargate native routes.

    Returns ``(status_code, json_or_raw, content_type)``.
    """
    base = (base_url if base_url is not None else project_ask_base_url()).rstrip("/")
    if not base:
        return (
            503,
            {
                "error": {
                    "code": "cdp_ask_unavailable",
                    "message": (
                        "PROJECT_ASK_URL not configured — start cdp-ask satellite"
                    ),
                }
            },
            "application/json",
        )
    async with make_async_client(base, timeout=timeout_s) as http:
        try:
            resp = await http.request(method, path, json=json_body)
        except httpx.RequestError as exc:
            return (
                502,
                {
                    "error": {
                        "code": "cdp_ask_unreachable",
                        "message": str(exc)[:300],
                    }
                },
                "application/json",
            )
        media = resp.headers.get("content-type", "application/json")
        if "application/json" in media and resp.content:
            try:
                return resp.status_code, resp.json(), media
            except ValueError:
                return resp.status_code, resp.content, media
        return resp.status_code, resp.content, media
