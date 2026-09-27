"""Hub reader for the cdp_ask registry document (GET /v1/project-ask/registry)."""

from __future__ import annotations

import http.client
import json
import os
import threading
import time
from typing import Any
from urllib.parse import urlparse

_CACHE_TTL_S = 2.0

_cache_lock = threading.Lock()
_cached_doc: dict[str, Any] | None = None
_cached_at: float | None = None

_DEFAULT_BASE_URL = "http://10.0.0.76:8770"


def _project_ask_base_url() -> str:
    explicit = os.environ.get("PROJECT_ASK_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    return _DEFAULT_BASE_URL.rstrip("/")


def _fetch_registry_document() -> dict[str, Any]:
    base = _project_ask_base_url()
    parsed = urlparse(base)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = "/v1/project-ask/registry"
    if parsed.path and parsed.path != "/":
        path = f"{parsed.path.rstrip('/')}{path}"

    conn = http.client.HTTPConnection(host, port, timeout=0.5)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read()
    finally:
        conn.close()

    if response.status != 200:
        return _unavailable()
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _unavailable()
    if not isinstance(payload, dict):
        return _unavailable()
    availability = str(payload.get("availability") or "")
    if availability not in ("ok", "unavailable"):
        return _unavailable()
    return payload


def _unavailable() -> dict[str, Any]:
    return {
        "availability": "unavailable",
        "seats": None,
        "seat_count": None,
    }


def read_fleet_registry(*, force_refresh: bool = False) -> dict[str, Any]:
    """Return the registry document; never an empty dict or load_active."""
    global _cached_doc, _cached_at
    now = time.monotonic()
    with _cache_lock:
        if (
            not force_refresh
            and _cached_doc is not None
            and _cached_at is not None
            and now - _cached_at <= _CACHE_TTL_S
        ):
            return dict(_cached_doc)
    try:
        doc = _fetch_registry_document()
    except OSError:
        doc = _unavailable()
    with _cache_lock:
        _cached_doc = dict(doc)
        _cached_at = time.monotonic()
    return dict(doc)


def _reset_fleet_registry_cache_for_tests() -> None:
    global _cached_doc, _cached_at
    with _cache_lock:
        _cached_doc = None
        _cached_at = None
