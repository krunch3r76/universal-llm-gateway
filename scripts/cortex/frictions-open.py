#!/usr/bin/env python3
"""List open frictions (F5 friction→skill funnel).

Uses cortex dispatch ``frictions`` (service:* assertions, optional category).
"""

from __future__ import annotations

import argparse
import json
import sys

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client


def main() -> int:
    parser = argparse.ArgumentParser(description="List open frictions (F5)")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--service", help="Narrow to service:mcp-server, etc.")
    parser.add_argument(
        "--category",
        default="tool_error",
        help="Friction category prefix in claim (default: tool_error)",
    )
    parser.add_argument("--seeded-by", dest="seeded_by", help="Filing agent slug")
    args = parser.parse_args()

    body: dict[str, object] = {
        "tool": "frictions",
        "arguments": json.dumps(
            {
                "limit": args.limit,
                **({"service": args.service} if args.service else {}),
                **({"category": args.category} if args.category else {}),
                **({"seeded_by": args.seeded_by} if args.seeded_by else {}),
            }
        ),
    }

    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=10.0) as client:
            r = client.post("/dispatch", json=body)
            r.raise_for_status()
            data = r.json()
            items = data.get("items", [])
    except Exception as e:
        print(f"Error querying cortex: {e}", file=sys.stderr)
        return 1

    print(f"Open frictions: {len(items)} (limit={args.limit})")
    for row in items:
        aid = row.get("id")
        eid = row.get("entity_id", "")
        claim = (row.get("claim") or "")[:120]
        by = row.get("seeded_by") or "?"
        print(f"  [{aid}] {eid} ({by}) {claim}")
    print(
        '\nClose: cortex(tool="friction_close", '
        'arguments=\'{"assertion_id": ID, "resolution_kind": "agent_skill:slug"}\')'
    )
    print("Bus queue: agent_bus list_threads with tags=[type:bug]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
