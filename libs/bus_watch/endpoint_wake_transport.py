"""HTTP client and delivery-outcome table for the endpoint wake dispatcher."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

_RETRIABLE_DISPLAY = {
    "active_chat_busy",
    "agent_id_mismatch",
    "composer_not_empty",
    "debug_unreachable",
    "no_active_chat",
    "no_composer",
    "no_page",
    "surface_probe_failed",
    "unknown",
}
_CSE_PENDING_ERRORS = {"no_identity", "project_ask_unconfigured"}
_GIW = os.environ.get("GIT_INTEGRATION_WORKER_URL", "http://127.0.0.1:8091")
_MAX_PARENT_HOPS = 3
_COMPOSER_IMPRESARIO_PREFIX = "composer-impresario-"


def composer_impresario_suffix(agent: str) -> str | None:
    """Return the agent_id token suffix from ``composer-impresario-<token>``."""
    raw = str(agent or "").strip().lower()
    if not raw.startswith(_COMPOSER_IMPRESARIO_PREFIX):
        return None
    suffix = raw[len(_COMPOSER_IMPRESARIO_PREFIX) :].strip()
    return suffix or None


_BARE_GLASS_AGENT_ID_SUFFIX = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){0,4}[0-9a-f]{0,12}$")


def glass_agent_id_suffix_candidates(
    agent: str | None, *, raw_to: str | None = None
) -> list[str]:
    """Suffix tokens for Glass ``agent_id`` lookup (impresario alias or bare uuid prefix)."""
    seen: set[str] = set()
    out: list[str] = []
    for value in (agent, raw_to):
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        imp = composer_impresario_suffix(text)
        if imp is not None and imp not in seen:
            seen.add(imp)
            out.append(imp)
            continue
        lowered = text.lower()
        if _BARE_GLASS_AGENT_ID_SUFFIX.fullmatch(lowered) and lowered not in seen:
            seen.add(lowered)
            out.append(lowered)
    return out


def agent_id_matches_composer_suffix(agent_id: str, suffix: str) -> bool:
    """True when ``suffix`` matches the start of a Glass ``agent_id`` UUID."""
    aid = str(agent_id or "").strip().lower()
    suf = str(suffix or "").strip().lower()
    if not aid or not suf:
        return False
    if aid.startswith(suf):
        return True
    return aid.replace("-", "").startswith(suf.replace("-", ""))


def classify_orion(body: Any) -> tuple[str, str]:
    """Map an orion-chat-wake pipeline result onto delivery outcomes."""
    if not isinstance(body, dict):
        return "unknown", "unparseable"
    reason = str(body.get("reason") or "")
    if body.get("ok") and body.get("delivered"):
        return "delivered", reason or "delivered"
    if reason == "deferred_streaming":
        return "pending", reason
    if reason in {"cdp_unreachable", "conversation_missing"}:
        return "pending", reason
    if reason == "orion_echo_drop":
        return "skipped", reason
    if reason in {"approval_unrecognized", "non_connector_dialog"}:
        return "refused", reason
    if body.get("pasted") and not body.get("delivered"):
        return "unknown", reason or "delivery_unconfirmed"
    if body.get("ok") is False:
        return "refused", reason or "refused"
    return "unknown", reason or "unnamed"


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

    def lane_parent(self, thread_id: str) -> str | None:
        """Return lane-current parent_thread, or None when the thread is unbound or the read fails."""
        try:
            payload = self._bus("GET", f"/threads/{thread_id}/lane-current")
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        parent = payload.get("parent_thread")
        if not isinstance(parent, str) or not parent.strip():
            return None
        return parent.strip()

    def _subscribed_rows(self, agent: str) -> list[dict[str, Any]]:
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

    def subscription(
        self, agent: str, thread_id: str, *, raw_to: str | None = None
    ) -> tuple[str, dict[str, Any]] | None:
        rows = self._subscribed_rows(agent)
        if composer_impresario_suffix(agent) is not None or (
            not rows and agent != "cursor"
        ):
            rows = self._subscribed_rows("cursor")
        ancestors: list[str] = []
        seen: set[str] = {thread_id}
        cur: str | None = thread_id
        for _hop in range(_MAX_PARENT_HOPS):
            if not cur:
                break
            try:
                payload_lane = self._bus("GET", f"/threads/{cur}/lane-current")
            except Exception:
                break
            parent = (
                payload_lane.get("parent_thread")
                if isinstance(payload_lane, dict)
                else None
            )
            if not isinstance(parent, str) or parent == "" or parent in seen:
                break
            ancestors.append(parent)
            seen.add(parent)
            cur = parent

        ancestor_rank = {scope: idx + 1 for idx, scope in enumerate(ancestors)}

        def _rank(scope: str) -> int:
            if scope == thread_id:
                return 0
            if scope in ancestor_rank:
                return ancestor_rank[scope]
            if scope == "*":
                return 4
            return 99

        subscribed = [r for r in rows if r.get("state") == "subscribed"]
        candidates = [
            r
            for r in subscribed
            if r.get("thread_scope") in {thread_id, "*"}
            or str(r.get("thread_scope") or "") in ancestor_rank
        ]
        if not candidates:
            for suffix in glass_agent_id_suffix_candidates(agent, raw_to=raw_to):
                resolved = self._subscription_by_cursor_agent_id_suffix(suffix)
                if resolved is not None:
                    return resolved
            return None
        best = min(
            candidates,
            key=lambda r: (
                _rank(str(r.get("thread_scope") or "")),
                -int(r.get("id") or 0),
            ),
        )
        scope = str(best.get("thread_scope") or "")
        if scope == thread_id:
            kind = "exact"
        elif scope == "*":
            kind = "wildcard"
        else:
            kind = "parent"
        return kind, best

    def _subscription_by_cursor_agent_id_suffix(
        self, suffix: str
    ) -> tuple[str, dict[str, Any]] | None:
        """Glass tab bind for worker CLOSEOUT to ``composer-impresario-<id>``."""
        payload = self._bus(
            "GET",
            "/endpoint-subscriptions",
            params={"agent": "cursor"},
        )
        rows = [
            row
            for row in payload.get("subscriptions") or []
            if row.get("state") == "subscribed"
            and agent_id_matches_composer_suffix(
                str(row.get("agent_id") or ""), suffix
            )
        ]
        if not rows:
            return None
        best = max(rows, key=lambda r: int(r.get("id") or 0))
        return "agent_id", best

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

    def turns(self, thread: str, **params: Any) -> dict[str, Any]:
        payload = self._bus(
            "GET",
            "/turns",
            params={"thread": thread, **params},
        )
        return payload if isinstance(payload, dict) else {"turns": payload}

    def create_turn(
        self,
        *,
        thread: str,
        from_agent: str,
        to_agent: str,
        subject: str,
        body: str,
    ) -> dict[str, Any]:
        """Insert one bus turn (same contract as ``POST /turns``)."""
        return self._bus(
            "POST",
            "/turns",
            json={
                "thread": thread,
                "from": from_agent,
                "to": to_agent,
                "subject": subject,
                "body": body,
            },
        )

    def unsubscribe(
        self,
        agent: str,
        thread_scope: str,
        *,
        bound_by: str = "endpoint-wake-dispatcher",
        evidence: str | None = None,
        expected_agent_id: str | None = None,
    ) -> dict[str, Any]:
        return self._bus(
            "POST",
            "/endpoint-subscriptions/unsubscribe",
            json={
                "agent": agent,
                "thread_scope": thread_scope,
                "bound_by": bound_by,
                "evidence": evidence,
                "expected_agent_id": expected_agent_id,
            },
        )
