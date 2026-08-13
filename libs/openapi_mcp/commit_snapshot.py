"""Pre-commit OpenAPI operands from the resulting commit tree (git index).

``check_manifest`` reads the working-tree file and ``create_app()`` imports
working-tree routes. Path-explicit commits then false-PASS (both worktree
sides agree, the index does not) or false-FATAL (unstaged foreign WIP). This
module reads index blobs for the manifest and imports live OpenAPI from a
materialized index snapshot so the gate judges the commit, not the checkout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from openapi_mcp.codegen import (
    AdapterManifest,
    ManifestCheckResult,
    compare_binding_drift,
    compare_schema_drift,
    parse_manifest_source,
)

MANIFEST_RELPATH: dict[str, str] = {
    "cortex": "libs/cortex_store/openapi_mcp/generated_adapter_manifest.py",
    "agent-bus": "libs/agent_bus_store/openapi_mcp/generated_adapter_manifest.py",
    "rag": "services/rag/openapi_mcp/generated_adapter_manifest.py",
    "giw": "services/git_integration_worker/openapi_mcp/generated_adapter_manifest.py",
}

LiveLoader = Callable[[list[str], Path], dict[str, AdapterManifest]]

_LIVE_CHILD = r"""
import json, os, sys
root = os.environ["OPENAPI_SNAPSHOT_ROOT"]
for p in (
    root,
    os.path.join(root, "libs"),
    os.path.join(root, "services", "mcp-server"),
    os.path.join(root, "services", "universal-stargate"),
):
    if p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)

def _schema(service: str) -> dict:
    if service == "cortex":
        from cortex_store.main import create_app
        return create_app().openapi()
    if service == "agent-bus":
        from agent_bus_store.server import create_app
        return create_app().openapi()
    if service == "rag":
        from services.rag.rag_service.main import app
        return app.openapi()
    if service == "giw":
        from services.git_integration_worker.app import create_app
        return create_app().openapi()
    raise SystemExit(f"unknown service {service!r}")

def _manifest(service: str, schema: dict) -> dict:
    if service == "cortex":
        from cortex_store.openapi_mcp.codegen import generate_adapter_manifest
    elif service == "agent-bus":
        from agent_bus_store.openapi_mcp.codegen import generate_adapter_manifest
    elif service == "rag":
        from services.rag.openapi_mcp.codegen import generate_adapter_manifest
    else:
        from services.git_integration_worker.openapi_mcp.codegen import (
            generate_adapter_manifest,
        )
    m = generate_adapter_manifest(schema)
    return {
        "openapi_sha256": m.openapi_sha256,
        "served_ops": m.served_ops,
        "non_binding_path_fingerprints": m.non_binding_path_fingerprints,
        "facade_tool": m.facade_tool,
    }

services = json.loads(sys.argv[1])
out_path = os.environ["OPENAPI_SNAPSHOT_OUT"]
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump({s: _manifest(s, _schema(s)) for s in services}, fh)
"""


def git_show(repo: Path, spec: str) -> str | None:
    """Return ``git show <spec>`` text, or None when the object is absent."""
    out = subprocess.run(
        ["git", "show", spec],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return None
    return out.stdout


def resulting_commit_text(repo: Path, relpath: str) -> str | None:
    """Blob text for *relpath* as ``git commit`` would record it (index)."""
    return git_show(repo, f":{relpath}")


def materialize_index(repo: Path, dest: Path) -> None:
    """Checkout every index path into *dest* so live imports cannot see WT WIP."""
    dest.mkdir(parents=True, exist_ok=True)
    prefix = str(dest.resolve()) + "/"
    subprocess.run(
        ["git", "checkout-index", "-a", "-f", f"--prefix={prefix}"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def check_committed_bindings(
    committed: AdapterManifest,
    live: AdapterManifest,
) -> ManifestCheckResult:
    """Compare explicit committed vs live manifests without reading a worktree path."""
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


def _payload_to_manifest(payload: dict) -> AdapterManifest:
    return AdapterManifest(
        openapi_sha256=str(payload["openapi_sha256"]),
        served_ops=dict(payload["served_ops"]),
        non_binding_path_fingerprints=dict(
            payload.get("non_binding_path_fingerprints") or {}
        ),
        facade_tool=str(payload.get("facade_tool") or "cortex"),
    )


def load_live_manifests_from_index(
    services: list[str],
    *,
    repo: Path,
    python: str | None = None,
) -> dict[str, AdapterManifest]:
    """Import each service's OpenAPI from a materialized index snapshot.

    sitecustomize prepends the live checkout ``libs/``; the child re-inserts
    the snapshot root at ``sys.path[0]`` so unstaged route modules cannot win.
    """
    if not services:
        return {}
    python = python or sys.executable
    with tempfile.TemporaryDirectory(prefix="openapi-index-") as tmp:
        dest = Path(tmp) / "tree"
        materialize_index(repo, dest)
        out_path = Path(tmp) / "live.json"
        env = os.environ.copy()
        env["OPENAPI_SNAPSHOT_ROOT"] = str(dest.resolve())
        env["OPENAPI_SNAPSHOT_OUT"] = str(out_path)
        proc = subprocess.run(
            [python, "-c", _LIVE_CHILD, json.dumps(list(services))],
            cwd=str(repo),
            capture_output=True,
            text=True,
            env=env,
        )
        if proc.returncode != 0 or not out_path.is_file():
            err = (proc.stderr or proc.stdout or "snapshot live import failed")[-1500:]
            raise RuntimeError(err)
        try:
            payload = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"snapshot live JSON: {exc}") from exc
    return {name: _payload_to_manifest(body) for name, body in payload.items()}


def check_services_from_commit_tree(
    services: list[str],
    *,
    repo: Path,
    live_loader: LiveLoader | None = None,
) -> list[tuple[str, ManifestCheckResult]]:
    """Compare index manifests against live ops loaded from the same tree."""
    if live_loader is None:
        try:
            lives = load_live_manifests_from_index(services, repo=repo)
        except RuntimeError as exc:
            msg = f"FATAL: commit-tree live import failed: {exc}"
            return [(svc, ManifestCheckResult((msg,), ())) for svc in services]
    else:
        lives = live_loader(services, repo)
    results: list[tuple[str, ManifestCheckResult]] = []
    for service in services:
        relpath = MANIFEST_RELPATH.get(service)
        if relpath is None:
            results.append(
                (
                    service,
                    ManifestCheckResult(
                        (f"FATAL: unknown service {service!r}",),
                        (),
                    ),
                )
            )
            continue
        source = resulting_commit_text(repo, relpath)
        if source is None:
            results.append(
                (
                    service,
                    ManifestCheckResult(
                        (f"FATAL: missing manifest {relpath} in index",),
                        (),
                    ),
                )
            )
            continue
        live = lives.get(service)
        if live is None:
            results.append(
                (
                    service,
                    ManifestCheckResult(
                        (f"FATAL: no commit-tree live manifest for {service}",),
                        (),
                    ),
                )
            )
            continue
        committed = parse_manifest_source(source)
        results.append((service, check_committed_bindings(committed, live)))
    return results
