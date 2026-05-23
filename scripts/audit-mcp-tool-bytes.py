#!/usr/bin/env python3
"""Audit per-tool byte cost of the MCP tool catalog as advertised on the wire.

Boots the FastMCP server in-process (no network, no auth), enumerates the
primary tools, serializes each one as it would appear in the MCP
``tools/list`` response, and reports byte sizes — total, per-tool,
description-only, schema-only.

Also reports byte determinism: serializes the catalog twice and diffs the
output to identify per-turn variance that would bust a prompt cache.

Outputs:
  - Summary table sorted by total bytes desc
  - Pareto: top 5 tools and their share of total
  - Determinism verdict
  - Optional --json dump of the full per-tool record set

Usage:
  python scripts/audit-mcp-tool-bytes.py [--json out.json] [--include-overflow]

Run from the universal-llm-gateway venv with PYTHONPATH including
services/mcp-server (the repo's sitecustomize.py wires libs/ but the
mcp-server tree is normally only on path inside the container).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_SERVER_DIR = REPO_ROOT / "services" / "mcp-server"


def _bootstrap_path() -> None:
    """Inject mcp-server source into sys.path so its flat imports resolve."""
    sys.path.insert(0, str(MCP_SERVER_DIR))
    os.environ.setdefault("MCP_AUTH_TOKEN", "audit-noop")
    os.environ.setdefault("MCP_OAUTH_DISABLED", "1")


def _serialize_tool(tool: Any) -> dict[str, Any]:
    """Render a tool exactly as the MCP ``tools/list`` response would.

    FastMCP's ``FunctionTool`` exposes ``to_mcp_tool()`` which produces the
    on-wire shape (``name`` / ``description`` / ``inputSchema``); the
    inputSchema has already been minified by ``schema_compact``.
    """
    if hasattr(tool, "to_mcp_tool"):
        return tool.to_mcp_tool().model_dump(exclude_none=True, by_alias=True)
    if hasattr(tool, "model_dump"):
        return tool.model_dump(exclude_none=True, by_alias=True)
    return {
        "name": getattr(tool, "name", "?"),
        "description": getattr(tool, "description", ""),
        "inputSchema": getattr(tool, "inputSchema", {}),
    }


def _measure(record: dict[str, Any]) -> dict[str, int]:
    name_b = len(record.get("name", "").encode("utf-8"))
    desc_b = len((record.get("description") or "").encode("utf-8"))
    schema = record.get("inputSchema") or {}
    schema_b = len(
        json.dumps(schema, separators=(",", ":"), default=str).encode("utf-8")
    )
    total_b = len(
        json.dumps(record, separators=(",", ":"), default=str).encode("utf-8")
    )
    return {
        "name_b": name_b,
        "desc_b": desc_b,
        "schema_b": schema_b,
        "total_b": total_b,
    }


def _collect(include_overflow: bool) -> list[dict[str, Any]]:
    from server import _PRIMARY_TOOLS, _build_server

    mcp = _build_server()
    tools = asyncio.run(mcp.list_tools())

    rows: list[dict[str, Any]] = []
    for t in tools:
        rec = _serialize_tool(t)
        is_primary = rec.get("name") in _PRIMARY_TOOLS
        if not is_primary and not include_overflow:
            continue
        sizes = _measure(rec)
        rows.append(
            {
                "name": rec.get("name"),
                "primary": is_primary,
                **sizes,
                "description": rec.get("description"),
                "inputSchema": rec.get("inputSchema"),
            }
        )
    return rows


def _determinism_check(include_overflow: bool) -> tuple[bool, int]:
    """Render twice, diff the canonical JSON. Returns (deterministic, drift_bytes)."""
    a = _collect(include_overflow)
    b = _collect(include_overflow)
    ja = json.dumps(
        [{k: v for k, v in r.items() if k != "primary"} for r in a],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    jb = json.dumps(
        [{k: v for k, v in r.items() if k != "primary"} for r in b],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    if ja == jb:
        return True, 0
    drift = sum(1 for x, y in zip(ja, jb, strict=False) if x != y) + abs(
        len(ja) - len(jb)
    )
    return False, drift


def _print_table(rows: list[dict[str, Any]]) -> None:
    rows = sorted(rows, key=lambda r: r["total_b"], reverse=True)
    total = sum(r["total_b"] for r in rows)
    desc_total = sum(r["desc_b"] for r in rows)
    schema_total = sum(r["schema_b"] for r in rows)

    print(f"\n{'name':<28} {'total':>8} {'desc':>8} {'schema':>8} {'%total':>7}")
    print("-" * 64)
    for r in rows:
        pct = 100.0 * r["total_b"] / total if total else 0
        print(
            f"{r['name']:<28} {r['total_b']:>8} {r['desc_b']:>8} "
            f"{r['schema_b']:>8} {pct:>6.1f}%"
        )
    print("-" * 64)
    print(
        f"{'TOTAL':<28} {total:>8} {desc_total:>8} {schema_total:>8} {'100.0':>6}%"
    )
    print(f"\nTools: {len(rows)}    Total: {total} bytes ({total / 1024:.1f} KiB)")

    print("\nPareto (top 5):")
    cum = 0
    for r in rows[:5]:
        cum += r["total_b"]
        print(
            f"  {r['name']:<28} {r['total_b']:>6} bytes "
            f"({100.0 * r['total_b'] / total:.1f}% / cum {100.0 * cum / total:.1f}%)"
        )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--json", type=Path, help="dump full records to JSON")
    p.add_argument(
        "--include-overflow",
        action="store_true",
        help="also measure non-primary (dispatch-routed) tools",
    )
    args = p.parse_args()

    _bootstrap_path()
    rows = _collect(args.include_overflow)
    _print_table(rows)

    deterministic, drift = _determinism_check(args.include_overflow)
    print(
        f"\nDeterminism: {'OK (byte-identical across renders)' if deterministic else f'DRIFT — {drift} bytes differ between two renders'}"
    )

    if args.json:
        args.json.write_text(
            json.dumps(rows, indent=2, ensure_ascii=False, default=str)
        )
        print(f"\nWrote per-tool records to {args.json}")


if __name__ == "__main__":
    main()
