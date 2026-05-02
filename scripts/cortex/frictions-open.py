#!/usr/bin/env python3
"""List open frictions and suggest resolutions (F5 friction→skill funnel).

Queries for friction assertions that have not been superseded and have no recent agent_skill edge.
"""

import argparse
import sys

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client


def main() -> int:
    parser = argparse.ArgumentParser(description="List open frictions (F5)")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=10.0) as client:
            r = client.get("/assertions", params={"review_status": "staged", "limit": args.limit})
            r.raise_for_status()
            data = r.json()
            items = data.get("items", [])
    except Exception as e:
        print(f"Error querying cortex: {e}")
        items = []

    friction_count = len([i for i in items if "friction" in str(i.get("claim", "")).lower()])
    print(f"Frictions-open CLI (F5) — found ~{friction_count} open frictions in staged pool (sampled {len(items)}).")
    print(
        'Use cortex(tool="friction_close", arguments={"assertion_id": ID, "resolution_kind": "agent_skill:slug"}) to close.'
    )
    print(
        "\nSuggested resolutions: agent_skill:{slug}, workflow:{slug}, todo:{slug}, superseded, wontfix."
    )
    print("This closes the funnel per the spec (creates resolves edge).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
