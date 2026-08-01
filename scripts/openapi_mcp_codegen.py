#!/usr/bin/env python3
"""Build-time OpenAPI → MCP adapter codegen entry (OMDR + W0 generalize)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "libs"))
sys.path.insert(0, str(_REPO / "services" / "mcp-server"))

from agent_bus_store.openapi_mcp import codegen as agent_bus_codegen  # noqa: E402
from cortex_store.openapi_mcp.census import (  # noqa: E402
    build_four_bucket_census,
    render_census_markdown,
)
from cortex_store.openapi_mcp.codegen import (  # noqa: E402
    check_generated_module,
    dry_run_generate,
    write_generated_module,
)
from openapi_mcp.binding import extract_typed_routes, inject_x_mcp  # noqa: E402
from openapi_mcp.registry import default_registry  # noqa: E402

_GENERATED_OPENAPI = _REPO / "config" / "mcp" / "generated" / "cortex.openapi.json"
_SERVICE_CHOICES = ("cortex", "agent-bus", "all")


def _load_service_schema(service: str) -> dict[str, Any]:
    if service == "cortex":
        from cortex_store.main import create_app

        return create_app().openapi()
    if service == "agent-bus":
        from agent_bus_store.server import create_app

        return create_app().openapi()
    raise ValueError(f"unknown service {service!r}")


def _check_service(service: str) -> bool:
    schema = _load_service_schema(service)
    if service == "cortex":
        return check_generated_module(schema)
    return agent_bus_codegen.check_generated_module(schema)


def _write_service(service: str) -> Path:
    schema = _load_service_schema(service)
    if service == "cortex":
        manifest = dry_run_generate(schema)
        return write_generated_module(manifest)
    manifest = agent_bus_codegen.dry_run_generate(schema)
    return agent_bus_codegen.write_generated_module(manifest)


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
            manifest = dry_run_generate(schema)
        else:
            manifest = agent_bus_codegen.dry_run_generate(schema)
        print(
            f"served_ops={len(manifest.served_ops)} "
            f"sha256={manifest.openapi_sha256[:12]}…"
        )
        return 0

    if args.write:
        if service == "all":
            cortex_path = _write_service("cortex")
            agent_bus_path = _write_service("agent-bus")
            print(f"wrote {cortex_path}")
            print(f"wrote {agent_bus_path}")
            return 0
        path = _write_service(service)
        print(f"wrote {path}")
        return 0

    if args.check:
        if service == "all":
            cortex_ok = _check_service("cortex")
            agent_bus_ok = _check_service("agent-bus")
            if not cortex_ok:
                print("check failed: cortex", file=sys.stderr)
            if not agent_bus_ok:
                print("check failed: agent-bus", file=sys.stderr)
            return 0 if cortex_ok and agent_bus_ok else 1
        return 0 if _check_service(service) else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
