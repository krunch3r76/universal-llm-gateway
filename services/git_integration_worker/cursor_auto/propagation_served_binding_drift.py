"""Served-vs-HEAD binding drift gate — op identity on live OpenAPI surfaces."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openapi_mcp.codegen import (
    ManifestCheckResult,
    compare_binding_drift,
    parse_manifest_source,
)

from services.git_integration_worker.cursor_auto.propagation_served_artifact import (
    probe_served_artifact,
)

_REPO = Path(__file__).resolve().parents[3]

_CODEGEN_TO_PROBE_SLUG: dict[str, str] = {
    "cortex": "cortex_api",
    "agent-bus": "agent_bus",
    "rag": "rag",
    "giw": "git_integration_worker",
}

_MANIFEST_RELPATH: dict[str, str] = {
    "cortex": "libs/cortex_store/openapi_mcp/generated_adapter_manifest.py",
    "agent-bus": "libs/agent_bus_store/openapi_mcp/generated_adapter_manifest.py",
    "rag": "services/rag/openapi_mcp/generated_adapter_manifest.py",
    "giw": "services/git_integration_worker/openapi_mcp/generated_adapter_manifest.py",
}

_BINDING_KEYS = ("method", "path", "operation_id")


def _binding_identity(meta: dict[str, str]) -> dict[str, str]:
    """Canonical op binding fields for served-vs-HEAD drift comparison."""
    return {key: meta[key] for key in _BINDING_KEYS}


def _git_show_head(relpath: str) -> str | None:
    out = subprocess.run(
        ["git", "show", f"HEAD:{relpath}"],
        cwd=_REPO,
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return None
    return out.stdout


def load_head_manifest_served_ops(service: str) -> dict[str, dict[str, str]] | None:
    """Load ``SERVED_OPS`` from the HEAD commit manifest for one codegen service."""
    relpath = _MANIFEST_RELPATH.get(service)
    if relpath is None:
        return None
    source = _git_show_head(relpath)
    if source is None:
        return None
    return dict(parse_manifest_source(source).served_ops)


def check_served_binding_drift(
    services: list[str] | None = None,
    *,
    probe_fn: Callable[..., dict[str, Any] | None] | None = None,
    head_ops_fn: Callable[[str], dict[str, dict[str, str]] | None] | None = None,
    code_ref: str = "HEAD",
) -> ManifestCheckResult:
    """Compare live served op bindings to HEAD manifest op bindings.

    Any served-ahead op (live − HEAD) or served-behind op (HEAD − live) or
    path/method mismatch → FATAL via :func:`compare_binding_drift`.
    """
    probe = probe_fn or probe_served_artifact
    head_loader = head_ops_fn or load_head_manifest_served_ops
    checked = services or sorted(_CODEGEN_TO_PROBE_SLUG)
    fatals: list[str] = []
    for service in checked:
        probe_slug = _CODEGEN_TO_PROBE_SLUG.get(service)
        if probe_slug is None:
            fatals.append(f"{service}: served binding drift unknown service")
            continue
        payload = probe(probe_slug, code_ref=code_ref)
        if payload is None:
            fatals.append(f"{service}: served binding drift probe unreachable")
            continue
        if not payload.get("byte_identical"):
            fatals.append(f"{service}: served binding drift surfaces not byte-identical")
            continue
        served_ops = payload.get("served_ops")
        if not isinstance(served_ops, dict):
            fatals.append(f"{service}: served binding drift unreadable served_ops")
            continue
        head_ops = head_loader(service)
        if head_ops is None:
            fatals.append(f"{service}: served binding drift HEAD manifest unreadable")
            continue
        normalized_head = {op: _binding_identity(meta) for op, meta in head_ops.items()}
        normalized_served = {
            op: _binding_identity(meta) for op, meta in served_ops.items()
        }
        for msg in compare_binding_drift(normalized_head, normalized_served):
            fatals.append(f"{service}: {msg}")
    return ManifestCheckResult(fatal_messages=tuple(fatals), warning_messages=())


__all__ = [
    "check_served_binding_drift",
    "load_head_manifest_served_ops",
]
