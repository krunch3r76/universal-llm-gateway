"""Capability catalog, invoke, and satellite relay routes.

Local pipeline members and the satellite vocabulary share ``/capabilities``.
A query ``id`` or ``category`` reads the local tree. A path category that is
in the satellite vocabulary relays upstream. Anything else is a local member.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import ValidationError
from transport_utils import make_async_client

from systems.pipeline.registry.capability_source import build_tree

from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy
from .capability_events import (
    capability_relay_completed,
    capability_relay_failed,
    capability_vocabulary_rejected,
)
from .capability_vocabulary import (
    Category,
    bearer_for,
    load_vocabulary,
    map_origin,
    reset_vocabulary_cache,
    rewrite_json_value,
    rewrite_link_header,
)
from .git import _filter_request_headers, _filter_response_headers
from .pipelines_dispatch import DispatchRequest, _error_response, admit_dispatch

router = APIRouter(tags=["capabilities"])

# At least as long as the longest client deadline. triggers.py uses 600s.
_RELAY_TIMEOUT = 600.0


def _unavailable() -> JSONResponse:
    return _error_response(
        503, "pipeline_system_unavailable", "Pipeline execution unavailable"
    )


def _canonical_headers(response: JSONResponse, url: str) -> JSONResponse:
    response.headers["Content-Location"] = url
    response.headers["Link"] = f'<{url}>; rel="canonical"'
    return response


def _canonical_redirect(request: Request, url: str) -> RedirectResponse:
    query = urlencode(list(request.query_params.multi_items()))
    if query:
        url = f"{url}?{query}"
    return RedirectResponse(url=url, status_code=308)


def _accepted_with_links(response: JSONResponse, canonical_url: str) -> JSONResponse:
    """202 body links. monitor.href is copied from Location so the two match."""
    location = response.headers["location"]
    payload = json.loads(bytes(response.body))
    payload["links"] = {
        "monitor": {"href": location},
        "capability": {"href": canonical_url},
    }
    accepted = JSONResponse(status_code=response.status_code, content=payload)
    accepted.headers["Location"] = location
    return accepted


def _member_payload(tree, registry) -> dict[str, Any]:
    members = {member.id: member.summary for member in tree.members.values()}
    skips = list(registry.catalog_skips) + list(tree.skips)
    return {"members": members, "catalog_skips": skips}


def _not_found(tree, member_id: str) -> JSONResponse:
    return _error_response(
        404,
        "capability_not_found",
        f"Capability '{member_id}' is not registered.",
        data={"near_matches": tree.near_matches(member_id)},
    )


def _skipped(row: dict[str, str]) -> JSONResponse:
    return _error_response(
        404,
        "capability_skipped",
        f"Capability '{row.get('pipeline_id')}' was skipped ({row.get('reason')}).",
        data={"skip": row},
    )


def _local_ready(proxy: StargateProxy | None) -> bool:
    return (
        proxy is not None
        and proxy.pipeline_registry is not None
        and proxy.is_pipeline_system_ready
    )


def _proxy(request: Request) -> StargateProxy | None:
    override = request.app.dependency_overrides.get(get_proxy)
    if override is not None:
        return override()
    try:
        return get_proxy()
    except RuntimeError:
        return None


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
    bus = getattr(_proxy(request), "event_bus", None)
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


def _location_invalid(category: Category, location: str) -> JSONResponse:
    return JSONResponse(
        {
            "code": "UPSTREAM_LOCATION_INVALID",
            "message": "upstream Location is outside the origin prefix",
            "source": "master",
            "retryable": False,
            "data": {"category": category.name, "location": location},
        },
        status_code=502,
    )


def _method_not_allowed() -> JSONResponse:
    return JSONResponse(
        {
            "code": "method_not_allowed",
            "message": "DELETE is only relayed for satellite categories",
            "source": "master",
            "retryable": False,
            "data": {},
        },
        status_code=405,
    )


def _capability_not_found(category: str, member: str) -> JSONResponse:
    return JSONResponse(
        {
            "code": "capability_not_found",
            "message": "member path is not a satellite relay",
            "source": "master",
            "retryable": False,
            "data": {"category": category, "member": member},
        },
        status_code=404,
    )


async def _relay(
    request: Request,
    category: Category,
    member: str,
) -> Response:
    target = f"{category.origin_prefix}/{member}" if member else category.origin_prefix
    headers = _filter_request_headers(request.headers.raw)
    if category.auth_env:
        headers.pop("Authorization", None)
        headers.pop("authorization", None)
        token = bearer_for(category.auth_env)
        if token is None:
            await _publish(
                request,
                capability_relay_failed(
                    category=category.name,
                    member=member,
                    upstream=category.upstream_symbol,
                    error="auth_env_unconfigured",
                ),
            )
            return _upstream_unavailable(
                f"auth_env {category.auth_env} not configured"
            )
        if token:
            headers["Authorization"] = f"Bearer {token}"
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
    forwarded = _filter_response_headers(upstream.headers)
    location = forwarded.get("location") or forwarded.get("Location")
    if (
        200 <= upstream.status_code < 300
        and location
        and map_origin(location, category.origin_prefix, category.name) is None
    ):
        await _publish(
            request,
            capability_relay_failed(
                category=category.name,
                member=member,
                upstream=category.upstream_symbol,
                error="upstream_location_invalid",
            ),
        )
        return _location_invalid(category, location)
    content, forwarded = _rewrite_relay(
        content, forwarded, media, category
    )
    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=forwarded,
        media_type=media or None,
    )


def _rewrite_relay(
    content: bytes,
    headers: dict[str, str],
    media: str | None,
    category: Category,
) -> tuple[bytes, dict[str, str]]:
    """Rewrite Location, Link, and JSON href or OpenAPI paths onto the relay."""
    origin = category.origin_prefix
    name = category.name
    rewritten = dict(headers)
    for key, value in list(rewritten.items()):
        if key.lower() == "location":
            mapped = map_origin(value, origin, name)
            if mapped:
                rewritten[key] = mapped
        elif key.lower() == "link":
            rewritten[key] = rewrite_link_header(value, origin, name)
    if media and "json" in media.lower() and content:
        try:
            payload = json.loads(content)
        except Exception:
            return content, rewritten
        updated = rewrite_json_value(payload, origin, name)
        if updated == payload:
            return content, rewritten
        return json.dumps(updated).encode("utf-8"), rewritten
    return content, rewritten


def _local_list(
    proxy: StargateProxy | None,
    member_id: str | None,
    category: str | None,
) -> JSONResponse:
    if not _local_ready(proxy):
        return _unavailable()
    if member_id is not None and category is not None:
        return _error_response(
            422,
            "query_conflict",
            "Pass id or category, not both.",
        )
    registry = proxy.pipeline_registry
    tree = build_tree(proxy)
    if category is not None:
        if category not in registry._category_vocabulary:
            return _error_response(
                404,
                "capability_category_not_found",
                f"Category '{category}' is not in the vocabulary.",
            )
        members = {
            found_id: member.summary
            for found_id, member in tree.by_category(category).items()
        }
        description = registry._category_vocabulary.descriptions.get(category, "")
        return JSONResponse(
            status_code=200,
            content={"description": description, "members": members},
        )
    if member_id is not None:
        member = tree.resolve(member_id)
        if member is None:
            row = next(
                (
                    skip
                    for skip in registry.catalog_skips
                    if skip.get("pipeline_id") == member_id
                ),
                None,
            )
            if row is not None:
                return _skipped(row)
            return _not_found(tree, member_id)
        body = JSONResponse(status_code=200, content=member.summary)
        return _canonical_headers(body, member.canonical_url)
    return JSONResponse(status_code=200, content=_member_payload(tree, registry))


def _local_get(proxy: StargateProxy | None, member_id: str) -> JSONResponse:
    if not _local_ready(proxy):
        return _unavailable()
    tree = build_tree(proxy)
    member = tree.resolve(member_id)
    if member is None:
        return _not_found(tree, member_id)
    body = JSONResponse(status_code=200, content=member.summary)
    return _canonical_headers(body, member.canonical_url)


async def _local_post(
    request: Request,
    proxy: StargateProxy | None,
    member_id: str,
) -> JSONResponse:
    try:
        raw = await request.json()
    except Exception as exc:  # noqa: BLE001 — caller supplied invalid JSON
        return _error_response(
            400, "invalid_json", f"Request body is not valid JSON: {exc}"
        )
    try:
        dispatch = DispatchRequest.model_validate(raw)
    except ValidationError as exc:
        return _error_response(
            422, "validation_error", f"Invalid dispatch request: {exc.errors()}"
        )
    if dispatch.model != member_id:
        return _error_response(
            422,
            "capability_id_mismatch",
            f"Body model '{dispatch.model}' does not match path id '{member_id}'.",
        )
    if not _local_ready(proxy):
        return _unavailable()
    tree = build_tree(proxy)
    member = tree.resolve(member_id)
    if member is None:
        row = next(
            (
                skip
                for skip in proxy.pipeline_registry.catalog_skips
                if skip.get("pipeline_id") == member_id
            ),
            None,
        )
        if row is not None:
            return _skipped(row)
        return _not_found(tree, member_id)
    response = await admit_dispatch(request, proxy, dispatch)
    if response.status_code == 202:
        if response.headers.get("location"):
            return _accepted_with_links(response, member.canonical_url)
        return response
    return _canonical_headers(response, member.canonical_url)


@router.get("/capabilities")
async def list_capabilities(
    request: Request,
    _current_user: dict[str, object] = Depends(get_auth_dependency),
    id: str | None = Query(default=None),
    category: str | None = Query(default=None),
) -> JSONResponse:
    """Local member lookup when id or category is set; otherwise category links."""
    if id is not None or category is not None:
        return _local_list(_proxy(request), id, category)
    reset_vocabulary_cache()
    categories, skips = _categories()
    await _emit_skips(request, skips)
    listed = [
        {
            "name": cat.name,
            "description": cat.description,
            "implementation": cat.implementation,
            "href": f"/api/v1/capabilities/{cat.name}",
        }
        for cat in categories
    ]
    seen = {cat.name for cat in categories}
    proxy = _proxy(request)
    if _local_ready(proxy):
        tree = build_tree(proxy)
        descriptions = proxy.pipeline_registry._category_vocabulary.descriptions
        for name in sorted({member.category for member in tree.members.values()}):
            if name in seen:
                continue
            seen.add(name)
            listed.append(
                {
                    "name": name,
                    "description": descriptions.get(name, ""),
                    "implementation": "pipeline",
                    "href": f"/api/v1/capabilities/{name}",
                }
            )
    return JSONResponse({"categories": listed, "catalog_skips": skips})


@router.api_route("/capabilities/{first}", methods=["GET", "POST", "DELETE"])
async def capability_first(
    first: str,
    request: Request,
    _user: dict[str, object] = Depends(get_auth_dependency),
) -> Response:
    """Relay a satellite category, list a pipeline category, or 308 a bare id."""
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
            rebuilt = JSONResponse(body, status_code=upstream.status_code)
            for key in ("location", "link"):
                value = upstream.headers.get(key)
                if value:
                    rebuilt.headers[key] = value
            return rebuilt
        return upstream
    proxy = _proxy(request)
    if _local_ready(proxy):
        tree = build_tree(proxy)
        if first in proxy.pipeline_registry._category_vocabulary and tree.by_category(
            first
        ):
            return _local_list(proxy, None, first)
        member = tree.resolve(first)
        if member is not None:
            return _canonical_redirect(request, member.canonical_url)
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
    return _canonical_redirect(
        request, f"/api/v1/capabilities/{category.name}/{member}"
    )


@router.api_route(
    "/capabilities/{category}/{member:path}", methods=["GET", "POST", "DELETE"]
)
async def capability_member(
    category: str,
    member: str,
    request: Request,
    _user: dict[str, object] = Depends(get_auth_dependency),
) -> Response:
    """Relay a satellite category; otherwise redirect or admit a local member.

    Satellite match stays ahead of readiness. A local member whose category
    differs from the path is a 308 before the body is read. Anything else
    falls through to the existing GET or POST handler.
    """
    categories, _skips = _categories()
    match = next((cat for cat in categories if cat.name == category), None)
    if match is not None:
        return await _relay(request, match, member)
    if request.method == "DELETE":
        return _method_not_allowed()
    if "/" in member:
        return _capability_not_found(category, member)
    proxy = _proxy(request)
    if not _local_ready(proxy):
        return _unavailable()
    resolved = build_tree(proxy).resolve(member)
    if resolved is not None and resolved.category != category:
        return _canonical_redirect(request, resolved.canonical_url)
    if request.method == "POST":
        return await _local_post(request, proxy, member)
    return _local_get(proxy, member)
