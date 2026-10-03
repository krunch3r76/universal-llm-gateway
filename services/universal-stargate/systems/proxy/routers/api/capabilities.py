"""Canonical capability tree. Relays satellite categories and 308s bare ids."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from transport_utils import make_async_client

from ...dependencies import get_auth_dependency
from .capability_events import (
    capability_relay_completed,
    capability_relay_failed,
    capability_vocabulary_rejected,
)
from .capability_vocabulary import Category, load_vocabulary, reset_vocabulary_cache
from .git import _filter_request_headers, _filter_response_headers

router = APIRouter(tags=["capabilities"])

# At least as long as the longest client deadline. triggers.py uses 600s.
_RELAY_TIMEOUT = 600.0


def _upstream_unavailable(message: str) -> JSONResponse:
    return JSONResponse(
        {
            "code": "UPSTREAM_UNAVAILABLE",
            "message": message,
            "source": "master",
            "retryable": True,
            "data": {},
        },
        status_code=503,
    )


async def _publish(request: Request, event: Any) -> None:
    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:
        return
    await bus.publish_nowait(event)


async def _emit_skips(request: Request, skips: list[dict[str, str]]) -> None:
    for skip in skips:
        await _publish(
            request,
            capability_vocabulary_rejected(
                category=skip.get("category") or "",
                reason=skip.get("reason") or "",
            ),
        )


def _categories() -> tuple[list[Category], list[dict[str, str]]]:
    loaded = load_vocabulary()
    return loaded.categories, loaded.skips


async def _origin_listing(category: Category) -> dict[str, Any] | None:
    client = make_async_client(category.upstream_url(), timeout=_RELAY_TIMEOUT)
    try:
        resp = await client.get(category.origin_prefix)
    except httpx.HTTPError:
        return None
    finally:
        await client.aclose()
    if resp.status_code >= 400:
        return None
    try:
        body = resp.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


async def _relay(
    request: Request,
    category: Category,
    member: str,
) -> Response:
    target = f"{category.origin_prefix}/{member}" if member else category.origin_prefix
    headers = _filter_request_headers(request.headers.raw)
    body = await request.body()
    started = time.perf_counter()
    client = make_async_client(category.upstream_url(), timeout=_RELAY_TIMEOUT)
    try:
        upstream_req = client.build_request(
            method=request.method,
            url=target,
            params=request.query_params,
            headers=headers,
            content=body,
        )
        upstream = await client.send(upstream_req)
        content = upstream.content
    except httpx.HTTPError as exc:
        await _publish(
            request,
            capability_relay_failed(
                category=category.name,
                member=member,
                upstream=category.upstream_symbol,
                error=str(exc),
            ),
        )
        return _upstream_unavailable(str(exc))
    finally:
        await client.aclose()
    duration_ms = (time.perf_counter() - started) * 1000
    await _publish(
        request,
        capability_relay_completed(
            category=category.name,
            member=member,
            method=request.method,
            status=upstream.status_code,
            duration_ms=duration_ms,
        ),
    )
    media = upstream.headers.get("content-type")
    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=_filter_response_headers(upstream.headers),
        media_type=media or None,
    )


@router.get("/capabilities")
async def list_capabilities(
    request: Request,
    _user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """List categories from the vocabulary file."""
    reset_vocabulary_cache()
    categories, skips = _categories()
    await _emit_skips(request, skips)
    return JSONResponse(
        {
            "categories": [
                {
                    "name": cat.name,
                    "description": cat.description,
                    "implementation": cat.implementation,
                    "href": f"/api/v1/capabilities/{cat.name}",
                }
                for cat in categories
            ],
            "catalog_skips": skips,
        }
    )


@router.api_route("/capabilities/{first}", methods=["GET", "POST"])
async def capability_first(
    first: str,
    request: Request,
    _user: dict[str, object] = Depends(get_auth_dependency),
) -> Response:
    """Relay a category listing, or 308 a bare member id."""
    categories, skips = _categories()
    by_name = {cat.name: cat for cat in categories}
    if first in by_name:
        category = by_name[first]
        upstream = await _relay(request, category, "")
        if upstream.status_code >= 400:
            return upstream
        try:
            body = json.loads(upstream.body) if upstream.body else None
        except Exception:
            return upstream
        if isinstance(body, dict) and isinstance(body.get("members"), list):
            for member in body["members"]:
                if isinstance(member, dict) and member.get("name"):
                    member["href"] = (
                        f"/api/v1/capabilities/{category.name}/{member['name']}"
                    )
            return JSONResponse(body, status_code=upstream.status_code)
        return upstream
    owners: list[tuple[Category, str]] = []
    for category in categories:
        listing = await _origin_listing(category)
        if not listing:
            continue
        for member in listing.get("members") or []:
            if isinstance(member, dict) and member.get("name") == first:
                owners.append((category, first))
    if len(owners) != 1:
        await _emit_skips(request, skips)
        return JSONResponse(
            {
                "code": "UNKNOWN_MEMBER",
                "message": f"Unknown capability id: {first}",
                "source": "master",
                "retryable": False,
                "data": {"categories": [cat.name for cat in categories]},
            },
            status_code=404,
        )
    category, member = owners[0]
    query = urlencode(list(request.query_params.multi_items()))
    location = f"/api/v1/capabilities/{category.name}/{member}"
    if query:
        location = f"{location}?{query}"
    return RedirectResponse(url=location, status_code=308)


@router.api_route("/capabilities/{category}/{member}", methods=["GET", "POST"])
async def capability_member(
    category: str,
    member: str,
    request: Request,
    _user: dict[str, object] = Depends(get_auth_dependency),
) -> Response:
    """Relay one member to the origin unchanged."""
    categories, _skips = _categories()
    match = next((cat for cat in categories if cat.name == category), None)
    if match is None:
        return JSONResponse(
            {
                "code": "UNKNOWN_MEMBER",
                "message": f"Unknown category: {category}",
                "source": "master",
                "retryable": False,
                "data": {"categories": [cat.name for cat in categories]},
            },
            status_code=404,
        )
    return await _relay(request, match, member)
