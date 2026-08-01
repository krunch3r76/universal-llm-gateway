"""Shared OpenAPI MCP adapter manifest generation and two-tier drift check."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


@dataclass(frozen=True, slots=True)
class AdapterManifest:
    """Build-time manifest: binding map plus drift fingerprints."""

    openapi_sha256: str
    served_ops: dict[str, dict[str, str]]
    non_binding_path_fingerprints: dict[str, str]
    facade_tool: str


@dataclass(frozen=True, slots=True)
class ManifestCheckResult:
    """Two-tier drift outcome: FATAL binding vs WARNING schema-only."""

    fatal_messages: tuple[str, ...]
    warning_messages: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """True when no binding (FATAL) drift."""
        return not self.fatal_messages

    @property
    def exit_code(self) -> int:
        """0 when only WARNING-tier drift (or clean); 1 when FATAL."""
        return 1 if self.fatal_messages else 0


def openapi_sha256(openapi_schema: dict[str, Any]) -> str:
    payload = json.dumps(openapi_schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def binding_path_keys(served_ops: dict[str, dict[str, str]]) -> set[tuple[str, str]]:
    return {(meta["method"].upper(), meta["path"]) for meta in served_ops.values()}


def op_spec_fingerprint(spec: dict[str, Any]) -> str:
    payload = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_non_binding_fingerprints(
    openapi_schema: dict[str, Any],
    served_ops: dict[str, dict[str, str]],
) -> dict[str, str]:
    """Fingerprints for routes and top-level sections outside x-mcp bindings."""
    binding = binding_path_keys(served_ops)
    fingerprints: dict[str, str] = {}
    for path, methods in sorted((openapi_schema.get("paths") or {}).items()):
        if not isinstance(methods, dict):
            continue
        for method, spec in sorted(methods.items()):
            if method not in _HTTP_METHODS or not isinstance(spec, dict):
                continue
            m = method.upper()
            if (m, path) in binding:
                continue
            fingerprints[f"{m} {path}"] = op_spec_fingerprint(spec)
    for top_key in ("components", "info", "tags"):
        section = openapi_schema.get(top_key)
        if section:
            payload = json.dumps(section, sort_keys=True, separators=(",", ":"))
            fingerprints[f"@{top_key}"] = hashlib.sha256(payload.encode()).hexdigest()
    return fingerprints


def build_adapter_manifest(
    openapi_schema: dict[str, Any],
    served_ops: dict[str, dict[str, str]],
    *,
    facade_tool: str,
) -> AdapterManifest:
    return AdapterManifest(
        openapi_sha256=openapi_sha256(openapi_schema),
        served_ops=served_ops,
        non_binding_path_fingerprints=compute_non_binding_fingerprints(
            openapi_schema, served_ops
        ),
        facade_tool=facade_tool,
    )


def compare_binding_drift(
    committed: dict[str, dict[str, str]],
    live: dict[str, dict[str, str]],
) -> list[str]:
    """Return FATAL messages for op→(method,path) map drift."""
    messages: list[str] = []
    committed_ops = set(committed)
    live_ops = set(live)
    for op in sorted(committed_ops - live_ops):
        meta = committed[op]
        messages.append(
            f"FATAL: binding lost for op {op!r} "
            f"({meta['method']} {meta['path']})"
        )
    for op in sorted(live_ops - committed_ops):
        meta = live[op]
        messages.append(
            f"FATAL: unexpected binding for op {op!r} "
            f"({meta['method']} {meta['path']})"
        )
    for op in sorted(committed_ops & live_ops):
        if committed[op] != live[op]:
            messages.append(
                f"FATAL: binding drift for op {op!r}: "
                f"committed {committed[op]!r} vs live {live[op]!r}"
            )
    return messages


def compare_schema_drift(
    *,
    committed_sha256: str,
    live_sha256: str,
    committed_fingerprints: dict[str, str],
    live_fingerprints: dict[str, str],
) -> list[str]:
    """Return WARNING messages when schema drifted but bindings did not."""
    if committed_sha256 == live_sha256:
        return []
    if not committed_fingerprints:
        return [
            "WARNING: full OpenAPI SHA256 drift "
            f"({committed_sha256[:12]}…→{live_sha256[:12]}…) "
            "— non-binding schema changed; run --write to refresh fingerprints"
        ]
    warnings: list[str] = []
    all_keys = sorted(set(committed_fingerprints) | set(live_fingerprints))
    for key in all_keys:
        old = committed_fingerprints.get(key)
        new = live_fingerprints.get(key)
        if old == new:
            continue
        if old is None:
            warnings.append(f"WARNING: schema drift — added {key}")
        elif new is None:
            warnings.append(f"WARNING: schema drift — removed {key}")
        else:
            warnings.append(f"WARNING: schema drift — changed {key}")
    if not warnings:
        warnings.append(
            "WARNING: full OpenAPI SHA256 drift "
            f"({committed_sha256[:12]}…→{live_sha256[:12]}…) "
            "— run --write to refresh path fingerprints"
        )
    return warnings


def parse_manifest_module(path: Path) -> AdapterManifest:
    """Load committed manifest constants from a generated module path."""
    ns: dict[str, Any] = {}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
    return AdapterManifest(
        openapi_sha256=str(ns["OPENAPI_SHA256"]),
        served_ops=dict(ns["SERVED_OPS"]),
        non_binding_path_fingerprints=dict(
            ns.get("NON_BINDING_PATH_FINGERPRINTS") or {}
        ),
        facade_tool=str(ns.get("FACADE_TOOL", "cortex")),
    )


def check_manifest(
    live: AdapterManifest,
    *,
    manifest_path: Path,
) -> ManifestCheckResult:
    """Compare live manifest against on-disk committed module (two-tier)."""
    if not manifest_path.is_file():
        return ManifestCheckResult(
            fatal_messages=(f"FATAL: missing manifest {manifest_path}",),
            warning_messages=(),
        )
    committed = parse_manifest_module(manifest_path)
    fatal = compare_binding_drift(committed.served_ops, live.served_ops)
    warnings: list[str] = []
    if not fatal:
        warnings = compare_schema_drift(
            committed_sha256=committed.openapi_sha256,
            live_sha256=live.openapi_sha256,
            committed_fingerprints=committed.non_binding_path_fingerprints,
            live_fingerprints=live.non_binding_path_fingerprints,
        )
    return ManifestCheckResult(
        fatal_messages=tuple(fatal),
        warning_messages=tuple(warnings),
    )


def render_generated_module(manifest: AdapterManifest) -> str:
    """Render committed adapter manifest module source."""
    write_cmd = (
        f"python scripts/openapi_mcp_codegen.py --write --service {manifest.facade_tool}"
        if manifest.facade_tool != "cortex"
        else "python scripts/openapi_mcp_codegen.py --write"
    )
    check_cmd = (
        f"python scripts/openapi_mcp_codegen.py --check --service {manifest.facade_tool}"
        if manifest.facade_tool != "cortex"
        else "python scripts/openapi_mcp_codegen.py --check"
    )
    lines = [
        '"""Generated MCP adapter manifest — do not edit by hand.',
        "",
        "Regenerate:",
        f"  {write_cmd}",
        f"  {check_cmd}",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        f'OPENAPI_SHA256 = "{manifest.openapi_sha256}"',
        f'FACADE_TOOL = "{manifest.facade_tool}"',
        "SERVED_OPS: dict[str, dict[str, str]] = {",
    ]
    for op, meta in manifest.served_ops.items():
        lines.append(f'    "{op}": {{')
        for key, val in meta.items():
            lines.append(f'        "{key}": "{val}",')
        lines.append("    },")
    lines.append("}")
    lines.append(
        "NON_BINDING_PATH_FINGERPRINTS: dict[str, str] = {",
    )
    for key, fp in sorted(manifest.non_binding_path_fingerprints.items()):
        lines.append(f'    "{key}": "{fp}",')
    lines.extend(["}", ""])
    return "\n".join(lines)
