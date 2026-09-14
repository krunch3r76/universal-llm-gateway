#!/usr/bin/env python3
"""Backfill ``thread:{id}`` Cortex entities for existing ``role:root`` bus houses.

Lists roots missing ``thread:{id}`` with ``--dry-run``; ``--apply`` mints them
idempotently via ``agent_bus_store.cortex_thread_entity.mint_thread_entity``.
When ``document:{id}-continuity`` exists, a ``references`` edge is created.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from agent_bus_store.cortex_thread_entity import (  # noqa: E402
    list_role_root_threads_missing_entities,
    mint_thread_entity,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="List role:root bus ids lacking thread:{id} entities",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Mint missing thread:{id} entities (idempotent)",
    )
    args = parser.parse_args(argv)

    try:
        missing = list_role_root_threads_missing_entities()
    except Exception as exc:
        print(f"ERROR: failed to scan role:root threads: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        if not missing:
            print("All role:root bus threads have thread:{id} entities.")
            return 0
        print(f"Missing thread entities ({len(missing)}):")
        for row in missing:
            print(f"  {row['thread_id']:>6}  {row['slug'] or '-':30s}  {row['entity_id']}")
        return 0

    if not missing:
        print("Nothing to mint — all role:root threads already have entities.")
        return 0

    failures = 0
    created = 0
    for row in missing:
        thread_id = row["thread_id"]
        slug = row["slug"]
        try:
            result = mint_thread_entity(thread_id, slug)
        except Exception as exc:
            print(f"  FAIL  {thread_id}  {exc}", file=sys.stderr)
            failures += 1
            continue
        if result.get("created"):
            created += 1
            tag = "CREATE"
        else:
            tag = "EXISTS"
        edge = "edge" if result.get("edge_created") else "no-edge"
        print(f"  ok    {thread_id:>6}  [{tag}]  {edge}")

    print(f"Done: {created} created, {len(missing) - failures - created} already existed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
