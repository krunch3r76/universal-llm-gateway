#!/usr/bin/env python3
"""Build-time OpenAPI → MCP adapter codegen entry (OMDR + W0 generalize)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "libs"))
sys.path.insert(0, str(_REPO / "services" / "mcp-server"))

from agent_bus_store.openapi_mcp import codegen as agent_bus_codegen  # noqa: E402
from cortex_store.openapi_mcp import codegen as cortex_codegen  # noqa: E402
from cortex_store.openapi_mcp.census import (  # noqa: E402
    build_four_bucket_census,
    render_census_markdown,
)
from openapi_mcp.binding import extract_typed_routes, inject_x_mcp  # noqa: E402
from openapi_mcp.codegen import ManifestCheckResult  # noqa: E402
from openapi_mcp.registry import default_registry  # noqa: E402

from services.rag.openapi_mcp import codegen as rag_codegen  # noqa: E402

_GENERATED_OPENAPI = _REPO / "config" / "mcp" / "generated" / "cortex.openapi.json"
_SERVICE_CHOICES = ("cortex", "agent-bus", "rag", "all")

_SERVICE_PREFIXES: dict[str, tuple[str, ...]] = {
    "cortex": ("libs/cortex_store/",),
    "agent-bus": ("libs/agent_bus_store/",),
    "rag": ("services/rag/",),
}
_OPENAPI_TOUCH_MARKERS = ("/routes/", "/openapi_mcp/", "/main.py", "/server.py")


def _load_service_schema(service: str) -> dict[str, Any]:
    if service == "cortex":
        from cortex_store.main import create_app

        return create_app().openapi()
    if service == "agent-bus":
        from agent_bus_store.server import create_app

        return create_app().openapi()
    if service == "rag":
        from services.rag.rag_service.main import app

        return app.openapi()
    raise ValueError(f"unknown service {service!r}")


def _check_service_detailed(service: str) -> ManifestCheckResult:
    schema = _load_service_schema(service)
    if service == "cortex":
        return cortex_codegen.check_generated_module_detailed(schema)
    if service == "agent-bus":
        return agent_bus_codegen.check_generated_module_detailed(schema)
    if service == "rag":
        return rag_codegen.check_generated_module_detailed(schema)
    raise ValueError(f"unknown service {service!r}")


def _write_service(service: str) -> Path:
    schema = _load_service_schema(service)
    if service == "cortex":
        manifest = cortex_codegen.dry_run_generate(schema)
        return cortex_codegen.write_generated_module(manifest)
    if service == "agent-bus":
        manifest = agent_bus_codegen.dry_run_generate(schema)
        return agent_bus_codegen.write_generated_module(manifest)
    if service == "rag":
        manifest = rag_codegen.dry_run_generate(schema)
        return rag_codegen.write_generated_module(manifest)
    raise ValueError(f"unknown service {service!r}")


def _git_staged_paths() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def services_touched_by_staged(staged: list[str]) -> set[str]:
    """Map staged paths to openapi services; empty when nothing binding-related."""
    touched: set[str] = set()
    for rel in staged:
        for service, prefixes in _SERVICE_PREFIXES.items():
            if not any(rel.startswith(prefix) for prefix in prefixes):
                continue
            if any(marker in rel for marker in _OPENAPI_TOUCH_MARKERS):
                touched.add(service)
    return touched


def _emit_check_result(service: str, result: ManifestCheckResult) -> None:
    for msg in result.fatal_messages:
        print(f"{service}: {msg}", file=sys.stderr)
    for msg in result.warning_messages:
        print(f"{service}: {msg}", file=sys.stderr)


def _run_check(services: list[str]) -> int:
    exit_code = 0
    for service in services:
        result = _check_service_detailed(service)
        _emit_check_result(service, result)
        if result.exit_code != 0:
            exit_code = 1
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenAPI MCP adapter codegen")
    parser.add_argument(
        "--service",
        choices=_SERVICE_CHOICES,
        default=None,
        help="Which HTTP service manifest to read/write/check (default: cortex, or all for --check/--write)",
    )
    parser.add_argument("--write", action="store_true", help="Write generated manifest")
    parser.add_argument(
        "--check", action="store_true", help="Verify manifest matches OpenAPI"
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Pre-commit mode: check only services touched by staged openapi paths",
    )
    parser.add_argument(
        "--census",
        action="store_true",
        help="Print four-bucket census markdown to stdout",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run generator without writing (prints served op count)",
    )
    parser.add_argument(
        "--write-openapi",
        action="store_true",
        help=f"Write the served OpenAPI (with native x-mcp) to {_GENERATED_OPENAPI}",
    )
    parser.add_argument(
        "--unbound",
        action="store_true",
        help="List dispatch ops with no x-mcp route stamp and no exemption",
    )
    parser.add_argument(
        "--services",
        action="store_true",
        help="Dry-run every registered service (cortex + agent-bus today)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    service = args.service
    if service is None:
        service = "all" if args.check or args.write else "cortex"

    if args.census:
        census = build_four_bucket_census()
        print(render_census_markdown(census))
        return 0

    if args.unbound:
        if service == "agent-bus":
            from agent_bus_store.openapi_mcp._route_map import unbound_dispatch_ops

            unbound = unbound_dispatch_ops()
        elif service == "rag":
            from services.rag.openapi_mcp._route_map import unbound_dispatch_ops

            unbound = unbound_dispatch_ops()
        else:
            from cortex_store.openapi_mcp._route_map import unbound_dispatch_ops

            unbound = unbound_dispatch_ops()
        print(f"unbound ops: {len(unbound)}")
        for op in unbound:
            print(f"  {op}")
        return 0

    if args.services:
        for desc in default_registry():
            schema = desc.load_openapi()
            seed = desc.seed_bindings() if desc.seed_bindings else {}
            if seed:
                schema = inject_x_mcp(schema, dict(seed), tool=desc.facade_tool)
            n = len(extract_typed_routes(schema))
            print(
                f"{desc.name}: paths={len(schema.get('paths') or {})} "
                f"x-mcp-ops={n} facade={desc.facade_tool}"
            )
        return 0

    if args.write_openapi:
        from cortex_store.main import create_app

        schema = create_app().openapi()
        _GENERATED_OPENAPI.parent.mkdir(parents=True, exist_ok=True)
        _GENERATED_OPENAPI.write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        paths = schema.get("paths") or {}
        xm = sum(
            1
            for methods in paths.values()
            if isinstance(methods, dict)
            for spec in methods.values()
            if isinstance(spec, dict) and "x-mcp" in spec
        )
        print(f"wrote {_GENERATED_OPENAPI} paths={len(paths)} x-mcp-ops={xm}")
        return 0

    if args.dry_run:
        if service == "all":
            print("error: --dry-run requires a single --service", file=sys.stderr)
            return 2
        schema = _load_service_schema(service)
        if service == "cortex":
            manifest = cortex_codegen.dry_run_generate(schema)
        elif service == "agent-bus":
            manifest = agent_bus_codegen.dry_run_generate(schema)
        else:
            manifest = rag_codegen.dry_run_generate(schema)
        print(
            f"served_ops={len(manifest.served_ops)} "
            f"sha256={manifest.openapi_sha256[:12]}…"
        )
        return 0

    if args.write:
        if service == "all":
            cortex_path = _write_service("cortex")
            agent_bus_path = _write_service("agent-bus")
            rag_path = _write_service("rag")
            print(f"wrote {cortex_path}")
            print(f"wrote {agent_bus_path}")
            print(f"wrote {rag_path}")
            return 0
        path = _write_service(service)
        print(f"wrote {path}")
        return 0

    if args.check:
        if args.staged:
            touched = services_touched_by_staged(_git_staged_paths())
            if not touched:
                return 0
            return _run_check(sorted(touched))
        if service == "all":
            return _run_check(["cortex", "agent-bus", "rag"])
        return _run_check([service])

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
