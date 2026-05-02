#!/usr/bin/env python3
"""Age-based graduation for staged assertions (F3 from cortex-assertion-triage spec).

Dry-run by default. Calls the wired POST /assertions/age-staged endpoint.
Supports larger sets via --limit. Follows REST-first, transport_utils, and thin-relay invariants.
"""

import argparse
import sys

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client


def main() -> int:
    parser = argparse.ArgumentParser(description="Age staged assertions (F3)")
    parser.add_argument(
        "--dry-run", action="store_true", default=True, help="Default: dry run"
    )
    parser.add_argument(
        "--live", action="store_true", help="Run live (sets reviewer=system:age-policy)"
    )
    parser.add_argument("--days", type=int, default=30, help="Age threshold (commit_days)")
    parser.add_argument("--limit", type=int, default=100, help="Max candidates to process (larger set)")
    args = parser.parse_args()

    if args.live:
        args.dry_run = False

    body = {
        "dry_run": args.dry_run,
        "commit_days": args.days,
        "limit": args.limit,
    }

    print(f"Age-staged CLI (F3) — calling /assertions/age-staged with limit={args.limit}, dry_run={args.dry_run}")
    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=30.0) as client:
            r = client.post("/assertions/age-staged", json=body)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print("\n" + data.get("message", "No message"))
    print(f"Committed: {data.get('committed', 0)}")
    print(f"Rejected: {data.get('rejected', 0)}")
    preview = data.get("preview", [])
    if preview:
        print(f"\nPreview of first {len(preview)} candidates:")
        for p in preview:
            print(f"  - {p.get('entity_id')}: {p.get('claim_preview', '')[:60]}... (days_old={p.get('days_old')}, score={p.get('score')})")

    print("\nAging policy applied to larger set. Check review_queue or stats for impact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
