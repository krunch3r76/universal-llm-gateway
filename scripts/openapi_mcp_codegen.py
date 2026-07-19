#!/usr/bin/env python3
"""Build-time OpenAPI → MCP adapter codegen entry (OMDR-STRANGLER-S136)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "libs"))

from cortex_store.main import create_app  # noqa: E402
from cortex_store.openapi_mcp.census import (  # noqa: E402
    build_four_bucket_census,
    render_census_markdown,
)
from cortex_store.openapi_mcp.codegen import (  # noqa: E402
    check_generated_module,
    dry_run_generate,
    write_generated_module,
)


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
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.census:
        census = build_four_bucket_census()
        print(render_census_markdown(census))
        return 0

    app = create_app()
    schema = app.openapi()

    if args.dry_run:
        manifest = dry_run_generate(schema)
        print(f"served_ops={len(manifest.served_ops)} sha256={manifest.openapi_sha256[:12]}…")
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
