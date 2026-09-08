#!/usr/bin/env python3
"""One-shot F1 repair: chain duplicate pipeline singletons on a continuity hub.

Keeps the newest active WATERMARK / MISSION / RESUME row per prefix (seeded_by
``continuity-consolidate``) and chains the rest via ``assertion_update``.

Usage:
    repair-continuity-hub-singletons.py 10223
    repair-continuity-hub-singletons.py document:10223-continuity
    repair-continuity-hub-singletons.py 10223 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from pipelines.continuity_consolidate.v1.handlers._cortex import (
    active_assertions,
    cortex_client,
    hub_entity_id,
)
from pipelines.continuity_consolidate.v1.handlers._plan import (
    WritePlan,
    singleton_active_counts,
)


def _resolve_hub_id(raw: str) -> str:
    text = raw.strip()
    if text.startswith("document:"):
        return text
    return hub_entity_id(text)


async def _run(hub_id: str, *, dry_run: bool) -> int:
    async with cortex_client() as client:
        before_rows = await active_assertions(client, hub_id)
        before = singleton_active_counts(before_rows)
        plan = WritePlan(dry_run=dry_run)
        chained = await plan.repair_pipeline_singletons(client, before_rows)
        after_rows = await active_assertions(client, hub_id)
        after = singleton_active_counts(after_rows)

    payload = {
        "hub_id": hub_id,
        "dry_run": dry_run,
        "before": before,
        "chained": chained,
        "after": after,
        "f1_pass": all(count <= 1 for count in after.values()),
        "errors": plan.errors(),
    }
    print(json.dumps(payload, indent=2))
    return 0 if payload["f1_pass"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "hub",
        help="Root thread id (e.g. 10223) or hub entity id (document:…-continuity)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate repair plan without assertion_update writes",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(_resolve_hub_id(args.hub), dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
