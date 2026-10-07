"""http_v1 step handler. Calls an allowlisted OpenAPI operation in process.

The allowlist key is the ``x-mcp`` op stored in YAML as ``operation_id``.
``coalesce_v1`` is not a leaf. A terminal await follows Location with
``wait=60`` and has no wall clock of its own.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx
from openapi_mcp.binding import TypedRoute, extract_typed_routes
from openapi_mcp.registry import ServiceDescriptor, default_registry
from systems.pipeline.core.handlers.protocol import AbstractStepHandler, StepOutput
from transport_utils import make_async_client

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

_PATH_VALUE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_TERMINAL = frozenset({"completed", "failed", "cancelled", "lost"})


class HttpHandler(AbstractStepHandler):
    """One allowlisted HTTP call, with an optional terminal job await and no wall clock."""

    step_type: str = "http_v1"

    def validate(self, step: StepConfig) -> list[str]:
        """Reject unknown services, unbound ops, and unstamped operations."""
        service = str(step.get_domain_field("service", "") or "")
        op = str(step.get_domain_field("operation_id", "") or "")
        descriptor = _descriptor(service)
        if descriptor is None:
            return [f"http_v1_service_unknown:{service}"]
        try:
            route = _route(descriptor, op)
        except ValueError as exc:
            return [str(exc)]
        if route is None:
            return [f"http_v1_operation_unknown:{op}"]
        if not route.pipeline:
            return [f"http_v1_operation_not_allowlisted:{op}"]
        return []

    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        """Resolve leaves, call the operation, and optionally await a run."""
        service = str(step.get_domain_field("service", "") or "")
        op = str(step.get_domain_field("operation_id", "") or "")
        descriptor = _descriptor(service)
        if descriptor is None:
            raise ValueError(f"http_v1_service_unknown:{service}")
        route = _route(descriptor, op)
        if route is None:
            raise ValueError(f"http_v1_operation_unknown:{op}")
        if not route.pipeline:
            raise ValueError(f"http_v1_operation_not_allowlisted:{op}")
        options = context.options
        path = _fill_path(route.path, _resolve_map(step.get_domain_field("path", {}) or {}, context, options))
        body = _resolve_map(step.get_domain_field("body", None), context, options)
        query = _resolve_map(step.get_domain_field("query", {}) or {}, context, options)
        headers = _headers(context, descriptor)
        response = await _send(descriptor.base_url, route.method, path, query, body, headers)
        mode = str(step.get_domain_field("await", "none") or "none")
        if mode != "terminal":
            return _output(response)
        if response.status_code != 202 or not response.headers.get("location"):
            raise ValueError("http_v1_await_without_monitor")
        stall = int(step.get_domain_field("stall_polls", 3) or 3)
        final = await _await_terminal(
            descriptor.base_url, response.headers["location"], headers, stall
        )
        status = str(final.get("status") or "")
        if status != "completed":
            raise ValueError(f"http_v1_run_{status or 'unknown'}")
        return StepOutput(raw="", json=final)


def _descriptor(name: str) -> ServiceDescriptor | None:
    for item in default_registry():
        if item.name == name:
            return item
    return None


def _route(descriptor: ServiceDescriptor, op: str) -> TypedRoute | None:
    routes = extract_typed_routes(descriptor.load_openapi())
    return routes.get(op)


def _resolve_map(node: Any, context: PipelineContext, options: dict[str, Any]) -> Any:
    if isinstance(node, dict) and "first_present" in node:
        if "coalesce_v1" in node:
            raise ValueError("coalesce_v1 is not an http_v1 leaf")
        for leaf in node["first_present"]:
            value = _resolve_map(leaf, context, options)
            if value is not None and value != "":
                return value
        return None
    if isinstance(node, dict) and "from" in node:
        return _from_ref(str(node["from"]), context, options)
    if isinstance(node, dict) and set(node) == {"literal"}:
        return node["literal"]
    if isinstance(node, dict):
        return {key: _resolve_map(value, context, options) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve_map(item, context, options) for item in node]
    return node


def _from_ref(ref: str, context: PipelineContext, options: dict[str, Any]) -> Any:
    if ref.startswith("options."):
        return options.get(ref.removeprefix("options."))
    if ref.startswith("steps."):
        rest = ref.removeprefix("steps.")
        step_id, _, dotted = rest.partition(".json.")
        output = context.get_output(step_id)
        cursor: Any = None if output is None else output.json
        for part in dotted.split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                return None
            cursor = cursor[part]
        return cursor
    raise ValueError(f"http_v1_ref_unknown:{ref}")


def _fill_path(template: str, params: Any) -> str:
    values = params if isinstance(params, dict) else {}
    def _sub(match: re.Match[str]) -> str:
        raw = values.get(match.group(1))
        text = "" if raw is None else str(raw)
        if _PATH_VALUE.fullmatch(text) is None:
            raise ValueError("http_v1_path_invalid")
        return quote(text, safe="")
    return re.sub(r"\{([^{}]+)\}", _sub, template)


def _headers(context: PipelineContext, descriptor: ServiceDescriptor) -> dict[str, str]:
    headers: dict[str, str] = {}
    request = getattr(context, "http_request", None)
    raw = getattr(request, "headers", None)
    surface = raw.get("x-ulg-surface") if raw is not None else None
    if surface:
        headers["X-ULG-Surface"] = str(surface)
    if descriptor.auth_env:
        token = os.environ.get(descriptor.auth_env, "")
        if not token:
            raise ValueError(f"http_v1_auth_unset:{descriptor.auth_env}")
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _send(
    base_url: str,
    method: str,
    path: str,
    query: Any,
    body: Any,
    headers: dict[str, str],
) -> httpx.Response:
    params = query if isinstance(query, dict) else None
    async with make_async_client(base_url, timeout=60.0) as client:
        return await client.request(
            method, path, params=params, json=body if body is not None else None, headers=headers
        )


def _output(response: httpx.Response) -> StepOutput:
    try:
        payload = response.json()
    except Exception:
        payload = None
    return StepOutput(raw=response.text, json=payload if isinstance(payload, dict) else None)


async def _await_terminal(
    base_url: str, location: str, headers: dict[str, str], stall_polls: int
) -> dict[str, Any]:
    previous: tuple[Any, Any] | None = None
    unchanged = 0
    while True:
        async with make_async_client(base_url, timeout=70.0) as client:
            response = await client.get(location, params={"wait": 60}, headers=headers)
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("http_v1_status_invalid")
        marker = (body.get("status"), body.get("progress_seq"))
        if body.get("status") in _TERMINAL:
            return body
        if marker == previous:
            unchanged += 1
            if unchanged >= stall_polls:
                raise ValueError("http_v1_stalled")
        else:
            unchanged = 0
            previous = marker
