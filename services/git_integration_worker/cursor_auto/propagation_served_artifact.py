"""Served OpenAPI artifact probes for propagation ``served_artifact`` proof."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

import httpx
from deploy_identity.code_ref_relation import (
    code_ref_relation_from_observed,
    code_ref_satisfied,
)
from openapi_mcp.binding import extract_typed_routes
from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_CORTEX_URL,
    DEFAULT_STARGATE_URL,
    make_sync_client,
    resolve_rag_base_url,
)

_GIW_HOST = os.environ.get("GIT_INTEGRATION_WORKER_HOST", "127.0.0.1")
_GIW_PORT = os.environ.get("GIT_INTEGRATION_WORKER_PORT", "8091")
_GIW_OPENAPI_DIRECT = os.environ.get(
    "GIT_INTEGRATION_WORKER_OPENAPI_URL",
    f"http://{_GIW_HOST}:{_GIW_PORT}/api/v1/git/openapi.json",
)
_GIW_OPENAPI_STARGATE = os.environ.get(
    "GIT_INTEGRATION_WORKER_OPENAPI_STARGATE_URL",
    f"{DEFAULT_STARGATE_URL.rstrip('/')}/api/v1/git/openapi.json",
)


@dataclass(frozen=True, slots=True)
class ServedSurface:
    """One client-reachable OpenAPI surface for a service."""

    name: str
    url: str


@dataclass(frozen=True, slots=True)
class ServedArtifactDescriptor:
    """Configured surfaces and external expectation for one service slug."""

    surfaces: tuple[ServedSurface, ...]
    expected_x_mcp_count: int


def _cortex_http_openapi_url() -> str | None:
    try:
        from scripts.model_manager.ui.controller.service_config import (
            cortex_api_http_bind,
        )

        host, port = cortex_api_http_bind()
        return f"http://{host}:{port}/openapi.json"
    except Exception:
        return None


def _cortex_surfaces() -> tuple[ServedSurface, ...]:
    surfaces = [
        ServedSurface("uds", f"{DEFAULT_CORTEX_URL.rstrip('/')}/openapi.json"),
    ]
    http_url = _cortex_http_openapi_url()
    if http_url:
        surfaces.append(ServedSurface("http_control_tower", http_url))
    return tuple(surfaces)


def _rag_openapi_url() -> str:
    base = resolve_rag_base_url().rstrip("/")
    return f"{base}/openapi.json"


SERVED_ARTIFACT_DESCRIPTORS: dict[str, ServedArtifactDescriptor] = {
    "git_integration_worker": ServedArtifactDescriptor(
        surfaces=(
            ServedSurface("direct_8091", _GIW_OPENAPI_DIRECT),
            ServedSurface("stargate_9999", _GIW_OPENAPI_STARGATE),
        ),
        expected_x_mcp_count=9,
    ),
    "cortex_api": ServedArtifactDescriptor(
        surfaces=_cortex_surfaces(),
        expected_x_mcp_count=46,
    ),
    "agent_bus": ServedArtifactDescriptor(
        surfaces=(
            ServedSurface(
                "uds",
                f"{DEFAULT_AGENT_BUS_URL.rstrip('/')}/openapi.json",
            ),
        ),
        expected_x_mcp_count=17,
    ),
    "rag": ServedArtifactDescriptor(
        surfaces=(ServedSurface("uds", _rag_openapi_url()),),
        expected_x_mcp_count=7,
    ),
}


def served_artifact_descriptor(service: str) -> ServedArtifactDescriptor | None:
    return SERVED_ARTIFACT_DESCRIPTORS.get(service)


def count_x_mcp_bindings(schema: dict[str, Any]) -> int:
    """Count path operations carrying an ``x-mcp`` stamp."""
    return len(extract_typed_routes(schema))


def _fetch_openapi_bytes(url: str, *, timeout_s: float = 5.0) -> bytes | None:
    try:
        if url.startswith("unix://"):
            sock_base = url[: url.index("/openapi.json")] if "/openapi.json" in url else url
            with make_sync_client(sock_base, timeout=timeout_s) as client:
                resp = client.get("/openapi.json")
        else:
            with httpx.Client(timeout=timeout_s) as client:
                resp = client.get(url)
        if resp.status_code != 200:
            return None
        return resp.content
    except (httpx.HTTPError, ValueError, OSError):
        return None


def _parse_openapi(raw: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def probe_served_artifact(
    service: str,
    *,
    code_ref: str,
    expected_x_mcp_count: int | None = None,
) -> dict[str, Any] | None:
    """Fetch every configured surface and build a composite proof payload."""
    descriptor = served_artifact_descriptor(service)
    if descriptor is None:
        return None
    expected = (
        expected_x_mcp_count
        if expected_x_mcp_count is not None
        else descriptor.expected_x_mcp_count
    )
    surface_results: dict[str, Any] = {}
    for surface in descriptor.surfaces:
        raw = _fetch_openapi_bytes(surface.url)
        if raw is None:
            return None
        schema = _parse_openapi(raw)
        if schema is None:
            return None
        surface_results[surface.name] = {
            "url": surface.url,
            "x_mcp_count": count_x_mcp_bindings(schema),
            "bytes_sha256": hashlib.sha256(raw).hexdigest(),
            "bytes_len": len(raw),
        }
    digests = {item["bytes_sha256"] for item in surface_results.values()}
    byte_identical = len(digests) == 1
    counts = {item["x_mcp_count"] for item in surface_results.values()}
    x_mcp_count = counts.pop() if len(counts) == 1 else None
    from services.git_integration_worker.cursor_auto.propagation_probe import (
        probe_process_live,
    )

    liveness = probe_process_live(service)
    observed_version = (
        liveness.get("code_version") if isinstance(liveness, dict) else None
    )
    relation = code_ref_relation_from_observed(code_ref, observed_version)
    return {
        "proof_class": "served_artifact",
        "surfaces": surface_results,
        "byte_identical": byte_identical,
        "x_mcp_count": x_mcp_count,
        "expected_x_mcp_count": expected,
        "code_version": observed_version,
        "code_ref": code_ref,
        "code_ref_relation": relation,
        "liveness": liveness,
    }


def _served_artifact_core_observed(
    payload: dict[str, Any],
    *,
    expected_x_mcp_count: int,
) -> bool:
    if not payload.get("byte_identical"):
        return False
    count = payload.get("x_mcp_count")
    if not isinstance(count, int) or count < expected_x_mcp_count:
        return False
    surfaces = payload.get("surfaces")
    return isinstance(surfaces, dict) and bool(surfaces)


def served_artifact_observed(
    payload: dict[str, Any] | None,
    *,
    code_ref: str,
    expected_x_mcp_count: int,
) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("proof_class") != "served_artifact":
        return False
    if not _served_artifact_core_observed(
        payload, expected_x_mcp_count=expected_x_mcp_count
    ):
        return False
    relation = payload.get("code_ref_relation")
    observed_version = payload.get("code_version")
    if relation == "unknown" or not isinstance(observed_version, str):
        return True
    return code_ref_satisfied(code_ref, observed_version)


__all__ = [
    "ServedArtifactDescriptor",
    "ServedSurface",
    "SERVED_ARTIFACT_DESCRIPTORS",
    "count_x_mcp_bindings",
    "probe_served_artifact",
    "served_artifact_descriptor",
    "served_artifact_observed",
]
