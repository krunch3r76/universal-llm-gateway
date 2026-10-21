"""HTTP client and delivery-outcome table for the endpoint wake dispatcher."""

from __future__ import annotations

import os
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

_RETRIABLE_DISPLAY = {
    "active_chat_busy",
    "composer_not_empty",
    "debug_unreachable",
    "no_composer",
    "no_page",
    "surface_probe_failed",
}
_CSE_PENDING_ERRORS = {"no_identity", "project_ask_unconfigured"}
_GIW = os.environ.get("GIT_INTEGRATION_WORKER_URL", "http://127.0.0.1:8091")


def classify_display(
    status_code: int, body: Any, error: BaseException | None
) -> tuple[str, str]:
    """Map a Stargate display-chat-wake outcome onto the rule-3 table."""
    if isinstance(error, httpx.ConnectError):
        return "pending", "connect"
    if error is not None or status_code >= 400:
        return "unknown", type(error).__name__ if error else f"http_{status_code}"
    if not isinstance(body, dict):
        return "unknown", "unparseable"
    reason = str(body.get("reason") or "")
    if reason == "paste_failed" or (body.get("pasted") and not body.get("delivered")):
        return "unknown", reason or "delivery_unconfirmed"
    if body.get("ok") and body.get("delivered"):
        return "delivered", reason or "delivered"
    if reason in _RETRIABLE_DISPLAY:
        return "pending", reason
    if body.get("ok") is False and not body.get("pasted"):
        return "refused", reason or "refused"
    return "unknown", reason or "unnamed"


def classify_cse(body: dict[str, Any]) -> tuple[str, str]:
    """Map a live fire_cse_followup body onto the rule-3 table."""
    if body.get("ok") is True:
        verified = body.get("send_verified")
        return "delivered", f"send_verified={verified}"
    error = body.get("error")
    if error in _CSE_PENDING_ERRORS:
        return "pending", str(error)
    status_code = body.get("status_code")
    if isinstance(status_code, int) and status_code >= 400:
        return "refused", str(error or status_code)
    return "unknown", str(error or "unnamed")


class StoreClient:
    """HTTP access to agent_bus and GIW through transport_utils."""

    def __init__(
        self, bus_url: str = DEFAULT_AGENT_BUS_URL, giw_url: str = _GIW
    ) -> None:
        self.bus_url = bus_url
        self.giw_url = giw_url

    def _bus(self, method: str, path: str, **kwargs: Any) -> Any:
        token = os.environ.get("AGENT_BUS_TOKEN", "")
        headers = dict(kwargs.pop("headers", {}) or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with make_sync_client(self.bus_url, timeout=30.0) as client:
            response = client.request(method, path, headers=headers, **kwargs)
        if response.status_code == 409:
            return {"conflict": True}
        response.raise_for_status()
        return response.json()

    def cursor(self) -> dict[str, Any]:
        return self._bus("GET", "/endpoint-wake/cursor")

    def since(self, after_id: int, limit: int = 50) -> list[dict[str, Any]]:
        payload = self._bus(
            "GET", "/turns/since", params={"after_id": after_id, "limit": limit}
        )
        return list(payload.get("turns") or [])

    def scan(self, expected: int, new_hw: int, pending: list[dict[str, Any]]) -> Any:
        return self._bus(
            "POST",
            "/endpoint-wake/scan",
            json={
                "consumer": "endpoint-wake-dispatcher",
                "expected_high_water": expected,
                "new_high_water": new_hw,
                "pending": pending,
            },
        )

    def claim(self) -> dict[str, Any]:
        return self._bus(
            "POST",
            "/endpoint-wake/claim",
            json={"debounce_s": 600, "claim_ttl_s": 300, "limit": 1},
        )

    def result(self, **payload: Any) -> Any:
        return self._bus("POST", "/endpoint-wake/result", json=payload)

    def holder(self, lane_thread_id: str) -> dict[str, Any] | None:
        with make_sync_client(self.giw_url, timeout=10.0) as client:
            response = client.get(
                "/api/v1/git/admin/cse-holders",
                params={"lane_thread_id": lane_thread_id},
            )
        response.raise_for_status()
        return response.json().get("holder")

    def subscription(
        self, agent: str, thread_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        payload = self._bus(
            "GET",
            "/endpoint-subscriptions",
            params={"agent": agent},
        )
        rows = [
            row
            for row in payload.get("subscriptions") or []
            if row.get("state") == "subscribed"
        ]
        parent_thread: str | None = None
        try:
            payload_lane = self._bus("GET", f"/threads/{thread_id}/lane-current")
            if isinstance(payload_lane, dict):
                parent_thread = payload_lane.get("parent_thread")
        except Exception:
            parent_thread = None

        def _rank(scope: str) -> int:
            if scope == thread_id:
                return 0
            if scope == "*":
                return 2
            if parent_thread and scope == parent_thread:
                return 1
            return 99

        subscribed = [r for r in rows if r.get("state") == "subscribed"]
        candidates = [
            r
            for r in subscribed
            if r.get("thread_scope") in {thread_id, "*"}
            or (parent_thread and r.get("thread_scope") == parent_thread)
        ]
        if not candidates:
            return None
        best = min(candidates, key=lambda r: (_rank(str(r.get("thread_scope") or "")), -int(r.get("id") or 0)))
        scope = str(best.get("thread_scope") or "")
        if scope == thread_id:
            kind = "exact"
        elif scope == "*":
            kind = "wildcard"
        else:
            kind = "parent"
        return kind, best

    def subscriptions(self, agent: str = "cursor") -> list[dict[str, Any]]:
        payload = self._bus(
            "GET",
            "/endpoint-subscriptions",
            params={"agent": agent},
        )
        return [
            row
            for row in payload.get("subscriptions") or []
            if row.get("state") == "subscribed"
        ]

    def unsubscribe(
        self,
        agent: str,
        thread_scope: str,
        *,
        bound_by: str = "endpoint-wake-dispatcher",
        evidence: str | None = None,
    ) -> dict[str, Any]:
        return self._bus(
            "POST",
            "/endpoint-subscriptions/unsubscribe",
            json={
                "agent": agent,
                "thread_scope": thread_scope,
                "bound_by": bound_by,
                "evidence": evidence,
            },
        )
