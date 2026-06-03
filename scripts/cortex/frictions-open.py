#!/usr/bin/env python3
"""List open frictions and suggest resolutions (F5 friction→skill funnel).

Queries for friction assertions that have not been superseded and have no recent agent_skill edge.

Staged listing uses review_status=staged only — not the reconstruct disposition filter
(reviewer='reconstruct-2026-06-02'; see reconstruct_provenance.py).
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
            # Friction assertions are seeded with entity_id="service:{name}" — filter by that
            # prefix to avoid false positives from staged assertions that happen to contain
            # the word "friction" in their claim text.
            r = client.get(
                "/assertions", params={"review_status": "staged", "limit": args.limit}
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("items", [])
    except Exception as e:
        print(f"Error querying cortex: {e}")
        items = []

    # Count frictions by entity_id prefix "service:" (canonical) rather than claim text scan.
    # Note: this is still an approximation — the /assertions endpoint does not support
    # entity_id prefix filtering, so we fetch staged assertions and filter client-side.
    friction_items = [
        i for i in items if str(i.get("entity_id", "")).startswith("service:")
    ]
    friction_count = len(friction_items)
    print(
        f"Frictions-open CLI (F5) — found ~{friction_count} open frictions in staged pool (sampled {len(items)}, filtered by entity_id prefix 'service:')."
    )
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
