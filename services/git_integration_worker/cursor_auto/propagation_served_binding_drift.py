"""Served-vs-working-tree binding drift gate — op identity on live OpenAPI surfaces.

On this fleet disk is executable (``decision:checkout-disk-is-executable``):
cursor-sdk dispatches run the live shared checkout; ``sync_restart`` respawns
with ``PYTHONPATH`` at the checkout; gateway bind-mounts source. Commit is not
the gate between edited and running — only ``sync_restart`` is. Therefore
served≠HEAD on a dirty checkout after restart is topology-expected, not a
defect; the primary signal is whether the running process is stale relative to
the working-tree manifest on disk.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from openapi_mcp.codegen import ManifestCheckResult, parse_manifest_source

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
    """Canonical op binding fields for served-vs-disk drift comparison."""
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


def load_worktree_manifest_served_ops(service: str) -> dict[str, dict[str, str]] | None:
    """Load ``SERVED_OPS`` from the on-disk manifest for one codegen service."""
    relpath = _MANIFEST_RELPATH.get(service)
    if relpath is None:
        return None
    path = _REPO / relpath
    if not path.is_file():
        return None
    return dict(parse_manifest_source(path.read_text()).served_ops)


def load_head_manifest_served_ops(service: str) -> dict[str, dict[str, str]] | None:
    """Load ``SERVED_OPS`` from the HEAD commit manifest for one codegen service."""
    relpath = _MANIFEST_RELPATH.get(service)
    if relpath is None:
        return None
    source = _git_show_head(relpath)
    if source is None:
        return None
    return dict(parse_manifest_source(source).served_ops)


def _split_binding_drift(
    reference: dict[str, dict[str, str]],
    served: dict[str, dict[str, str]],
    *,
    unexpected_tier: Literal["WARNING", "FATAL"],
) -> tuple[list[str], list[str]]:
    """Classify binding drift between a reference manifest and live served ops."""
    fatals: list[str] = []
    warnings: list[str] = []
    ref_ops = set(reference)
    served_ops = set(served)
    for op in sorted(ref_ops - served_ops):
        meta = reference[op]
        fatals.append(
            f"FATAL: binding lost for op {op!r} "
            f"({meta['method']} {meta['path']})"
        )
    for op in sorted(served_ops - ref_ops):
        meta = served[op]
        msg = (
            f"{unexpected_tier}: unexpected binding for op {op!r} "
            f"({meta['method']} {meta['path']})"
        )
        if unexpected_tier == "WARNING":
            warnings.append(msg)
        else:
            fatals.append(msg)
    for op in sorted(ref_ops & served_ops):
        if reference[op] != served[op]:
            fatals.append(
                f"FATAL: binding drift for op {op!r}: "
                f"reference {reference[op]!r} vs served {served[op]!r}"
            )
    return fatals, warnings


def _head_served_ahead_warnings(
    head: dict[str, dict[str, str]],
    served: dict[str, dict[str, str]],
) -> list[str]:
    """Foreign-WIP courtesy: served ops not yet committed at HEAD."""
    warnings: list[str] = []
    for op in sorted(set(served) - set(head)):
        meta = served[op]
        warnings.append(
            f"WARNING: unexpected binding for op {op!r} "
            f"({meta['method']} {meta['path']})"
        )
    return warnings


def check_served_binding_drift(
    services: list[str] | None = None,
    *,
    probe_fn: Callable[..., dict[str, Any] | None] | None = None,
    worktree_ops_fn: Callable[[str], dict[str, dict[str, str]] | None] | None = None,
    head_ops_fn: Callable[[str], dict[str, dict[str, str]] | None] | None = None,
    code_ref: str = "HEAD",
) -> ManifestCheckResult:
    """Compare live served op bindings to the working-tree manifest on disk.

    Primary signal (served vs working tree): served-behind and path/method
    mismatch → FATAL; served-ahead → WARNING. Secondary courtesy (served vs
    HEAD): served-ahead-of-HEAD → WARNING for uncommitted WIP visibility.
    """
    probe = probe_fn or probe_served_artifact
    worktree_loader = worktree_ops_fn or load_worktree_manifest_served_ops
    head_loader = head_ops_fn or load_head_manifest_served_ops
    checked = services or sorted(_CODEGEN_TO_PROBE_SLUG)
    fatals: list[str] = []
    warnings: list[str] = []
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
        worktree_ops = worktree_loader(service)
        if worktree_ops is None:
            fatals.append(
                f"{service}: served binding drift working-tree manifest unreadable"
            )
            continue
        normalized_worktree = {
            op: _binding_identity(meta) for op, meta in worktree_ops.items()
        }
        normalized_served = {
            op: _binding_identity(meta) for op, meta in served_ops.items()
        }
        svc_fatals, svc_warnings = _split_binding_drift(
            normalized_worktree,
            normalized_served,
            unexpected_tier="WARNING",
        )
        fatals.extend(f"{service}: {msg}" for msg in svc_fatals)
        warnings.extend(f"{service}: {msg}" for msg in svc_warnings)
        if normalized_worktree == normalized_served:
            head_ops = head_loader(service)
            if head_ops is not None:
                normalized_head = {
                    op: _binding_identity(meta) for op, meta in head_ops.items()
                }
                for msg in _head_served_ahead_warnings(
                    normalized_head, normalized_served
                ):
                    warnings.append(f"{service}: {msg}")
    return ManifestCheckResult(
        fatal_messages=tuple(fatals),
        warning_messages=tuple(warnings),
    )


__all__ = [
    "check_served_binding_drift",
    "load_head_manifest_served_ops",
    "load_worktree_manifest_served_ops",
]
