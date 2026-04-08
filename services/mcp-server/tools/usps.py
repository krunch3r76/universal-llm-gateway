"""USPS tracking tool — official USPS Tracking API integration.

Direct external API integration is implemented here because the USPS Tracking
API is a third-party HTTP surface, not an internal satellite service. The tool
is kept narrow and stateless: fetch OAuth token, query one tracking number,
normalize the shape, return the latest delivery state.
"""

from __future__ import annotations

import os
import re
import time
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from fastmcp import FastMCP

_API_BASE_URL = os.getenv("USPS_API_BASE_URL", "https://apis.usps.com").rstrip("/")
_CLIENT_ID_ENV = "USPS_CLIENT_ID"
_CLIENT_SECRET_ENV = "USPS_CLIENT_SECRET"
_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=10.0)
_TOKEN_REFRESH_SKEW_S = 60.0
_MIN_TRACKING_LEN = 10

_TOKEN_CACHE: dict[str, str | float] = {
    "access_token": "",
    "expires_at": 0.0,
}


def _credential_error() -> dict[str, str]:
    return {
        "error": (
            "USPS API credentials not configured. Set "
            f"{_CLIENT_ID_ENV} and {_CLIENT_SECRET_ENV} in the MCP server "
            "environment. The legacy Web Tools API retired in Jan 2026, and the "
            "public USPS tracking page now serves an anti-bot challenge, so a "
            "credential-free fallback is not reliable."
        )
    }


def _normalize_tracking_number(tracking_number: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]", "", tracking_number or "").upper()
    if len(normalized) < _MIN_TRACKING_LEN:
        raise ValueError(
            "tracking_number must contain at least 10 alphanumeric characters"
        )
    return normalized


def _get_access_token() -> str:
    client_id = os.getenv(_CLIENT_ID_ENV, "").strip()
    client_secret = os.getenv(_CLIENT_SECRET_ENV, "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(_credential_error()["error"])

    now = time.time()
    cached_token = str(_TOKEN_CACHE.get("access_token") or "")
    cached_expires_at = float(_TOKEN_CACHE.get("expires_at") or 0.0)
    if cached_token and cached_expires_at > (now + _TOKEN_REFRESH_SKEW_S):
        return cached_token

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }
    token_url = f"{_API_BASE_URL}/oauth2/v3/token"
    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.post(
            token_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )

    if response.status_code >= 400:
        detail = response.text[:500]
        raise RuntimeError(
            f"USPS OAuth token request failed ({response.status_code}): {detail}"
        )

    data = response.json()
    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("USPS OAuth token response did not include access_token")

    expires_in_raw = data.get("expires_in", 0)
    try:
        expires_in = float(expires_in_raw)
    except (TypeError, ValueError):
        expires_in = 0.0

    _TOKEN_CACHE["access_token"] = access_token
    _TOKEN_CACHE["expires_at"] = now + max(expires_in, 0.0)
    return access_token


def _coalesce_str(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _build_location(source: dict[str, Any]) -> dict[str, str | None]:
    city = _coalesce_str(source.get("eventCity"), source.get("destinationCity"))
    state = _coalesce_str(source.get("eventState"), source.get("destinationState"))
    zipcode = _coalesce_str(source.get("eventZIP"), source.get("destinationZIP"))
    country = _coalesce_str(source.get("eventCountry"))
    parts = [part for part in [city, state, zipcode, country] if part]
    return {
        "city": city,
        "state": state,
        "zip": zipcode,
        "country": country,
        "display": ", ".join(parts) if parts else None,
    }


def _normalize_status(status: str | None, category: str | None, summary: str | None) -> str:
    combined = " ".join(part for part in [status, category, summary] if part).lower()
    if "delivered" in combined:
        return "delivered"
    if "out for delivery" in combined:
        return "out_for_delivery"
    if "pickup" in combined or "held at post office" in combined:
        return "available_for_pickup"
    if any(
        marker in combined
        for marker in (
            "return",
            "exception",
            "alert",
            "undeliverable",
            "forwarded",
            "refused",
        )
    ):
        return "exception"
    if any(
        marker in combined
        for marker in (
            "accepted",
            "in possession",
            "label created",
            "shipping label created",
            "pre-shipment",
            "pre shipment",
        )
    ):
        return "accepted"
    if combined:
        return "in_transit"
    return "unknown"


def _extract_delivery_timestamp(
    events: list[dict[str, Any]],
    status: str | None,
    category: str | None,
) -> str | None:
    normalized = _normalize_status(status, category, None)
    if normalized != "delivered":
        return None
    for event in events:
        event_type = str(event.get("eventType") or "").lower()
        if "delivered" in event_type:
            return _coalesce_str(event.get("eventTimestamp"))
    if events:
        return _coalesce_str(events[0].get("eventTimestamp"))
    return None


def _shape_tracking_response(
    tracking_number: str, payload: dict[str, Any]
) -> dict[str, Any]:
    status = _coalesce_str(payload.get("status"))
    status_category = _coalesce_str(payload.get("statusCategory"))
    status_summary = _coalesce_str(payload.get("statusSummary"))
    events_raw = payload.get("trackingEvents")
    events: list[dict[str, Any]] = (
        [event for event in events_raw if isinstance(event, dict)]
        if isinstance(events_raw, list)
        else []
    )
    last_scan = events[0] if events else {}

    return {
        "tracking_number": tracking_number,
        "normalized_status": _normalize_status(status, status_category, status_summary),
        "status": status,
        "status_category": status_category,
        "status_summary": status_summary,
        "delivery_date": _extract_delivery_timestamp(events, status, status_category),
        "last_scan": {
            "event": _coalesce_str(last_scan.get("eventType"), status),
            "timestamp": _coalesce_str(last_scan.get("eventTimestamp")),
            "event_code": _coalesce_str(last_scan.get("eventCode")),
            "location": _build_location(last_scan or payload),
        },
        "origin": {
            "city": _coalesce_str(payload.get("originCity")),
            "state": _coalesce_str(payload.get("originState")),
            "zip": _coalesce_str(payload.get("originZIP")),
        },
        "destination": {
            "city": _coalesce_str(payload.get("destinationCity")),
            "state": _coalesce_str(payload.get("destinationState")),
            "zip": _coalesce_str(payload.get("destinationZIP")),
        },
        "mail_class": _coalesce_str(payload.get("mailClass")),
        "service": _coalesce_str(payload.get("services")),
        "proof_of_delivery_enabled": str(payload.get("proofOfDeliveryEnabled") or "")
        .lower()
        .strip()
        == "true",
        "raw": payload,
    }


def register_usps_tools(mcp: FastMCP) -> None:
    """Register USPS tracking tools on *mcp*."""

    @mcp.tool(title="USPS Track Package")
    def usps_track(tracking_number: str) -> dict[str, Any]:
        """Track one USPS package by tracking number.

        Use this when you already have an exact USPS tracking number and need an
        authoritative delivery state for legal evidence chains or mail follow-up.
        Prefer this over RAG or manual browser checks when the goal is focused
        status lookup. Requires USPS developer credentials in the MCP server
        environment; if they are absent, the tool returns an actionable error.
        """
        try:
            normalized_tracking_number = _normalize_tracking_number(tracking_number)
        except ValueError as exc:
            return {"error": str(exc)}

        try:
            access_token = _get_access_token()
        except RuntimeError as exc:
            return {"error": str(exc)}

        url = (
            f"{_API_BASE_URL}/tracking/v3/tracking/"
            f"{normalized_tracking_number}?expand=DETAIL"
        )
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                response = client.get(
                    url,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.TimeoutException:
            return {"error": "USPS tracking request timed out"}
        except httpx.RequestError as exc:
            return {"error": f"USPS tracking request failed: {exc}"}

        if response.status_code == 404:
            return {
                "tracking_number": normalized_tracking_number,
                "normalized_status": "not_found",
                "error": "Tracking number not found in USPS Tracking API",
            }
        if response.status_code >= 400:
            return {
                "tracking_number": normalized_tracking_number,
                "error": (
                    f"USPS tracking request failed ({response.status_code}): "
                    f"{response.text[:500]}"
                ),
            }

        try:
            payload = response.json()
        except ValueError:
            return {
                "tracking_number": normalized_tracking_number,
                "error": "USPS Tracking API returned invalid JSON",
            }
        if not isinstance(payload, dict):
            return {
                "tracking_number": normalized_tracking_number,
                "error": "USPS Tracking API returned non-object JSON",
            }

        return _shape_tracking_response(normalized_tracking_number, payload)
