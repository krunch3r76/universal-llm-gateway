#!/usr/bin/env python3
"""Generate committed skill source table from live Cortex entities (D1, generation-time only)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "libs"))

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

from implement_admission.skill_source_table import (
    CANONICAL_SLUG_ALIASES,
    CANONICAL_SKILL_SOURCE_URIS,
    TEMPLATE_VERSION,
    validate_generation_invariants,
)
from implement_admission.skill_table_freshness import _entity_source_uri, _preferred_live_uri


def _live_slugs(client) -> dict[str, str]:
    """Build slug → source_uri from live entities (prefer substantiated rule:)."""
    entries: dict[str, str] = {}
    resp = client.get("/entities", params={"type": "agent_skill", "limit": 500})
    if resp.status_code != 200:
        raise RuntimeError(f"entity list failed: {resp.status_code}")
    items = resp.json().get("items") or []
    for row in items:
        eid = str(row.get("id") or "")
        if not eid.startswith("agent_skill:"):
            continue
        slug = eid.removeprefix("agent_skill:")
        rule_resp = client.get(f"/entities/rule:{slug}?intent=full")
        if rule_resp.status_code == 200:
            uri = _entity_source_uri(rule_resp.json())
            if uri:
                entries[slug] = uri
                continue
        skill_resp = client.get(f"/entities/{eid}?intent=full")
        if skill_resp.status_code == 200:
            uri = _entity_source_uri(skill_resp.json())
            if uri:
                entries[slug] = uri
    return entries


def build_table(*, cortex_url: str = DEFAULT_CORTEX_URL) -> dict[str, str]:
    with make_sync_client(cortex_url, timeout=30.0) as client:
        live = _live_slugs(client)
    # Merge with committed keys so generation is additive-safe for hot-path slugs.
    merged = dict(CANONICAL_SKILL_SOURCE_URIS)
    merged.update(live)
    errors = validate_generation_invariants(merged, aliases=dict(CANONICAL_SLUG_ALIASES))
    if errors:
        raise RuntimeError("generation validation failed:\n" + "\n".join(errors))
    return dict(sorted(merged.items()))


def table_digest(entries: dict[str, str]) -> str:
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify committed table matches live")
    parser.add_argument("--print-digest", action="store_true", help="Print table digest only")
    args = parser.parse_args()

    if args.check:
        built = build_table()
        if built != dict(CANONICAL_SKILL_SOURCE_URIS):
            committed = set(CANONICAL_SKILL_SOURCE_URIS)
            live = set(built)
            added = live - committed
            removed = committed - live
            changed = {
                k
                for k in committed & live
                if CANONICAL_SKILL_SOURCE_URIS[k] != built[k]
            }
            print(
                f"DRIFT: added={sorted(added)} removed={sorted(removed)} "
                f"changed={sorted(changed)}",
                file=sys.stderr,
            )
            return 1
        print(f"OK template_version={TEMPLATE_VERSION} digest={table_digest(built)}")
        return 0

    built = build_table()
    digest = table_digest(built)
    if args.print_digest:
        print(digest)
        return 0
    print(json.dumps({"template_version": TEMPLATE_VERSION, "digest": digest, "entries": built}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
