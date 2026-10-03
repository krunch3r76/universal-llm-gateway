"""Capability catalog and invoke routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from systems.pipeline.registry.capability_source import build_tree

from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy
from .pipelines_dispatch import DispatchRequest, _error_response, admit_dispatch

router = APIRouter(tags=["capabilities"])


def _unavailable() -> JSONResponse:
    return _error_response(
        503, "pipeline_system_unavailable", "Pipeline execution unavailable"
    )


def _canonical_headers(response: JSONResponse, url: str) -> JSONResponse:
    response.headers["Content-Location"] = url
    response.headers["Link"] = f'<{url}>; rel="canonical"'
    return response


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


@router.get("/capabilities")
async def list_capabilities(
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
    id: str | None = Query(default=None),
    category: str | None = Query(default=None),
) -> JSONResponse:
    """List members, or resolve one id or one category."""
    if proxy.pipeline_registry is None or not proxy.is_pipeline_system_ready:
        return _unavailable()
    if id is not None and category is not None:
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
            member_id: member.summary
            for member_id, member in tree.by_category(category).items()
        }
        return JSONResponse(status_code=200, content={"members": members})
    if id is not None:
        member = tree.resolve(id)
        if member is None:
            row = next(
                (
                    skip
                    for skip in registry.catalog_skips
                    if skip.get("pipeline_id") == id
                ),
                None,
            )
            if row is not None:
                return _skipped(row)
            return _not_found(tree, id)
        body = JSONResponse(status_code=200, content=member.summary)
        return _canonical_headers(body, member.canonical_url)
    return JSONResponse(status_code=200, content=_member_payload(tree, registry))


@router.get("/capabilities/{category}/{member_id}")
async def get_capability(
    category: str,
    member_id: str,
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Resolve by id. A wrong category segment still returns the member."""
    if proxy.pipeline_registry is None or not proxy.is_pipeline_system_ready:
        return _unavailable()
    tree = build_tree(proxy)
    member = tree.resolve(member_id)
    if member is None:
        return _not_found(tree, member_id)
    body = JSONResponse(status_code=200, content=member.summary)
    return _canonical_headers(body, member.canonical_url)


@router.post("/capabilities/{category}/{member_id}")
async def invoke_capability(
    category: str,
    member_id: str,
    request: Request,
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Admit a run. Body model must match the path id."""
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
    if proxy.pipeline_registry is None or not proxy.is_pipeline_system_ready:
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
    return _canonical_headers(response, member.canonical_url)
