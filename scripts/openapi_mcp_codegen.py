#!/usr/bin/env python3
"""Build-time OpenAPI → MCP adapter codegen entry (OMDR + W0 generalize)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "libs"))

from cortex_store.openapi_mcp.census import (  # noqa: E402
    build_four_bucket_census,
    render_census_markdown,
)
from cortex_store.openapi_mcp.codegen import (  # noqa: E402
    check_generated_module,
    dry_run_generate,
    write_generated_module,
)
from openapi_mcp.binding import inject_x_mcp  # noqa: E402
from openapi_mcp.registry import default_registry  # noqa: E402

_GENERATED_OPENAPI = _REPO / "config" / "mcp" / "generated" / "cortex.openapi.json"


def _cortex_schema_with_x_mcp() -> dict:
    from cortex_store.main import create_app
    from cortex_store.openapi_mcp._route_map import mcp_route_seed

    return inject_x_mcp(create_app().openapi(), dict(mcp_route_seed()), tool="cortex")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenAPI MCP adapter codegen")
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
        help=f"Write x-mcp-enriched OpenAPI to {_GENERATED_OPENAPI}",
    )
    parser.add_argument(
        "--services",
        action="store_true",
        help="Dry-run every registered service (cortex + agent-bus today)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.census:
        census = build_four_bucket_census()
        print(render_census_markdown(census))
        return 0

    if args.services:
        for desc in default_registry():
            schema = desc.load_openapi()
            seed = desc.seed_bindings() if desc.seed_bindings else {}
            if seed:
                from openapi_mcp.binding import extract_typed_routes

                enriched = inject_x_mcp(schema, dict(seed), tool=desc.facade_tool)
                n = len(extract_typed_routes(enriched))
            else:
                from openapi_mcp.binding import extract_typed_routes

                n = len(extract_typed_routes(schema))
            print(
                f"{desc.name}: paths={len(schema.get('paths') or {})} "
                f"x-mcp-ops={n} facade={desc.facade_tool}"
            )
        return 0

    if args.write_openapi:
        schema = _cortex_schema_with_x_mcp()
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

    from cortex_store.main import create_app

    app = create_app()
    schema = app.openapi()

    if args.dry_run:
        manifest = dry_run_generate(schema)
        print(
            f"served_ops={len(manifest.served_ops)} "
            f"sha256={manifest.openapi_sha256[:12]}…"
        )
        return 0

    if args.write:
        manifest = dry_run_generate(schema)
        path = write_generated_module(manifest)
        print(f"wrote {path}")
        return 0

    if args.check:
        return 0 if check_generated_module(schema) else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
