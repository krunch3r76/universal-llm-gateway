#!/usr/bin/env python3
# ruff: noqa: E501

"""CLI for triaging staged assertions per F4 of cortex-assertion-triage-tooling.

Uses HTTP to /assertions with review_status=staged and groups results.
Follows REST-first invariant — no direct DB access.
"""

import argparse
import sys
from collections import defaultdict

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client


def main() -> int:
    parser = argparse.ArgumentParser(description="Triage staged assertions (F4)")
    parser.add_argument(
        "--by",
        choices=["entity", "warning", "age", "derivation_type", "domain"],
        default="entity",
        help="Group by",
    )
    parser.add_argument("--top", type=int, default=20, help="Top N entities/warnings")
    parser.add_argument(
        "--limit", type=int, default=100, help="Max assertions to fetch"
    )
    parser.add_argument("--dry-run", action="store_true", help="No changes")
    args = parser.parse_args()

    params = {"review_status": "staged", "limit": args.limit}

    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=10.0) as client:
            r = client.get("/assertions", params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        print(f"Error querying cortex: {e}", file=sys.stderr)
        return 1

    items = data.get("items", [])
    print(f"Staged assertions: {len(items)}")
    if not items:
        print("No staged assertions found.")
        return 0

    # Simple grouping by entity

    by_entity = defaultdict(list)
    for item in items:
        eid = item.get("entity_id", "unknown")
        by_entity[eid].append(item)

    print(f"\nTop entities with staged assertions (by={args.by}, top={args.top}):")
    for eid, group in sorted(by_entity.items(), key=lambda x: -len(x[1]))[: args.top]:
        print(f"  {eid}: {len(group)} staged")
        for i, item in enumerate(group[:3]):
            print(
                f"    - {item.get('claim', '')[:80]}... (confidence={item.get('confidence')})"
            )
        if len(group) > 3:
            print(f"    ... +{len(group) - 3} more")

    print(
        "\nSuggested action: review for missing reasoning_summary/chunk_id; use assertion_update to set review_status='committed' or supersede."
    )
    print("\nNext: run age-staged.py or use friction_close for related frictions (F5).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
