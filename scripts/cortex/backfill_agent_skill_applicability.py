#!/usr/bin/env python3
"""Backfill / re-partition `applicable_agents` on agent_skill entities.

The script is the canonical, re-runnable interface for revising the skill
partition. Two layers, override wins:

1. `PARTITION`: bucket → list of entity ids. Bucket `"*"` → `["*"]` applicable;
   any other bucket name (e.g. `"web"`, `"cursor"`) → `[bucket_name]`. Designed
   for the common single-bucket case; flat and human-readable.

2. `OVERRIDES`: entity_id → explicit `applicable_agents` list. Wins over the
   bucket-derived value. Use for multi-agent assignments (e.g.
   `["web", "cursor"]`) or any case the bucket layer cannot express.

Read-modify-write preserves existing attributes. Re-runs are idempotent —
entities already at the target value print `noop` and skip the PATCH.

Initial backfill follows agent-bus thread 882 turn 6 → Kaywan greenlight
("Backfill", turn 7): 18 universal / 9 web-only / 1 cursor-only.

Single-row revision workflow:
    1. Move id between PARTITION buckets, OR add an OVERRIDES entry.
    2. Dry-run: ~/.venvs/universal/bin/python scripts/cortex/backfill_agent_skill_applicability.py --dry-run
    3. Live:   ~/.venvs/universal/bin/python scripts/cortex/backfill_agent_skill_applicability.py

Audit (drift detection — Kaywan adds new and temp skills; the hardcoded
PARTITION will go stale otherwise):
    ~/.venvs/universal/bin/python scripts/cortex/backfill_agent_skill_applicability.py --audit
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request

CORTEX_SOCKET = "/tmp/universal-protocol/cortex-api.sock"


PARTITION: dict[str, list[str]] = {
    "*": [
        "agent_skill:architecture-invariants",
        "agent_skill:cortex-entity-restructure",
        "agent_skill:cortex-v24-implementation-arc",
        "agent_skill:document-ocr",
        "agent_skill:docx-ingestion",
        "agent_skill:email-bridge-mailbox",
        "agent_skill:financial-reasoning",
        "agent_skill:frontier-consult-offload",
        "agent_skill:frontier-dispatch",
        "agent_skill:image-video-generation",
        "agent_skill:jupiter-browser-via-mcp",
        "agent_skill:lawyer-stance",
        "agent_skill:legal-opinion-corpus-ingestion",
        "agent_skill:markdown-navigation",
        "agent_skill:gatherer-plan-discipline",
        "agent_skill:pre-deploy-gate-discipline",
        "agent_skill:session-close-audit",
        "agent_skill:thirdparty-api-mirror",
    ],
    "web": [
        "agent_skill:boe19p-appeal-discipline",
        "agent_skill:chase-escrow-discipline",
        "agent_skill:chase-escrow-statement-ingestion",
        "agent_skill:claudeburst-shadow-ops",
        "agent_skill:crypto-trading-research",
        "agent_skill:document-lifecycle-tracking",
        "agent_skill:hei-application-discipline",
        "agent_skill:tax",
        "agent_skill:w2-ingestion",
    ],
    "cursor": [
        "agent_skill:ulg-architecture",
    ],
}


# Multi-agent assignments. Wins over the bucket-derived value above. Use this
# for any skill whose applicability isn't a single agent or universal — e.g.
# `["web", "cursor"]` for legal corpus tooling that cursor occasionally pulls
# but gatherer/artisan don't need.
OVERRIDES: dict[str, list[str]] = {
    # "agent_skill:legal-opinion-corpus-ingestion": ["web", "cursor"],
    "agent_skill:xai-mcp-calling-shape": ["web-grok", "superheavy"],
}


class _UDSConnection:
    """Tiny UDS HTTP client — avoids dragging in httpx for a 28-entity batch."""

    def __init__(self, socket_path: str) -> None:
        import http.client
        import socket

        self._http_client = http.client
        self._socket_path = socket_path
        self._socket = socket

    def _connect(self):  # type: ignore[no-untyped-def]
        sock = self._socket.socket(self._socket.AF_UNIX, self._socket.SOCK_STREAM)
        sock.connect(self._socket_path)
        conn = self._http_client.HTTPConnection("localhost")
        conn.sock = sock
        return conn

    def request(
        self, method: str, path: str, body: dict | None = None
    ) -> tuple[int, dict]:
        conn = self._connect()
        try:
            headers = {"content-type": "application/json"} if body else {}
            payload = json.dumps(body).encode() if body is not None else None
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, (json.loads(data) if data else {})
        finally:
            conn.close()


def _slug_for(entity_id: str) -> str:
    """Resolve which partition bucket owns this entity. Single source of truth."""
    for slug, ids in PARTITION.items():
        if entity_id in ids:
            return slug
    raise KeyError(f"{entity_id} not in any partition bucket")


def _applicable_for(entity_id: str) -> tuple[list[str], str]:
    """Resolve `(applicable_agents, label)` for an entity.

    OVERRIDES wins over the bucket-derived assignment so multi-agent cases
    (e.g. `["web", "cursor"]`) can be expressed without warping the bucket
    layer. Label is for human-readable dry-run output.
    """
    if entity_id in OVERRIDES:
        agents = list(OVERRIDES[entity_id])
        return agents, f"override → {agents}"
    slug = _slug_for(entity_id)
    applicable = ["*"] if slug == "*" else [slug]
    label = {"*": "universal", "web": "web-only", "cursor": "cursor-only"}.get(
        slug, slug
    )
    return applicable, label


def _audit(client: _UDSConnection) -> int:
    """Read-only drift report between live cortex state and this script.

    Surfaces three classes:
      - Unpartitioned: agent_skill in DB but no entry in PARTITION/OVERRIDES.
        These get the COALESCE-default of `["*"]` via the SQL filter, which
        is a safe fallback but means the script won't track them on the
        next revision pass.
      - Drifted: PARTITION/OVERRIDES says X, live entity has Y. Either the
        live state was edited out-of-band, or PARTITION was reshuffled
        without a follow-up live run.
      - Orphan: id in PARTITION/OVERRIDES but no matching live entity
        (typically a deleted or renamed skill).
    """
    status, body = client.request(
        "GET", "/entities?type=agent_skill&limit=500"
    )
    if status != 200:
        print(f"AUDIT FAIL: GET /entities {status} → {body}")
        return 2
    live_summaries = body.get("items", [])
    live_ids = {row["id"] for row in live_summaries}
    partitioned = set(OVERRIDES.keys()) | {
        eid for ids in PARTITION.values() for eid in ids
    }

    unpartitioned = sorted(live_ids - partitioned)
    orphan = sorted(partitioned - live_ids)

    drifted: list[tuple[str, list[str] | None, list[str]]] = []
    for entity_id in sorted(partitioned & live_ids):
        expected, _label = _applicable_for(entity_id)
        status, body = client.request(
            "GET", f"/entities/{urllib.parse.quote(entity_id, safe=':')}"
        )
        if status != 200:
            continue
        attrs = body.get("attributes") or {}
        actual = attrs.get("applicable_agents") if isinstance(attrs, dict) else None
        if actual != expected:
            drifted.append((entity_id, actual, expected))

    print("Audit: agent_skill applicability")
    print(f"  Live skills total       : {len(live_ids)}")
    print(f"  In partition / overrides: {len(partitioned & live_ids)}")
    print(f"  Unpartitioned (default ['*'] via COALESCE): {len(unpartitioned)}")
    for eid in unpartitioned:
        print(f"    - {eid}")
    print(f"  Drifted (PARTITION ≠ live): {len(drifted)}")
    for eid, actual, expected in drifted:
        print(f"    - {eid}  live={actual}  expected={expected}")
    print(f"  Orphan partition entries (no live entity): {len(orphan)}")
    for eid in orphan:
        print(f"    - {eid}")

    return 0 if not (unpartitioned or drifted or orphan) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print intended writes without calling cortex-api PATCH.",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help=(
            "Read-only drift report: list live agent_skill entities not in "
            "PARTITION/OVERRIDES, drifted live values, and orphan partition "
            "entries. Exit 0 when clean, 1 when drift detected."
        ),
    )
    args = parser.parse_args()

    client = _UDSConnection(CORTEX_SOCKET)

    if args.audit:
        return _audit(client)

    all_ids = [eid for ids in PARTITION.values() for eid in ids]
    print(f"Backfilling applicable_agents on {len(all_ids)} agent_skill entities")
    if args.dry_run:
        print("DRY RUN — no writes will be issued")
    print()

    written = 0
    skipped = 0
    for entity_id in all_ids:
        applicable, label = _applicable_for(entity_id)

        status, body = client.request(
            "GET",
            f"/entities/{urllib.parse.quote(entity_id, safe=':')}",
        )
        if status != 200:
            print(f"  SKIP  {entity_id:60s}  [GET {status}]")
            skipped += 1
            continue

        existing = body.get("attributes") or {}
        if not isinstance(existing, dict):
            existing = {}
        prior = existing.get("applicable_agents")
        if prior == applicable:
            print(f"  noop  {entity_id:60s}  → {applicable}")
            continue
        merged = dict(existing)
        merged["applicable_agents"] = applicable

        if args.dry_run:
            print(
                f"  WOULD {entity_id:60s}  → {applicable}  ({label})"
                f"  prior={prior}"
            )
            continue

        status, body = client.request(
            "PATCH",
            f"/entities/{urllib.parse.quote(entity_id, safe=':')}",
            body={"attributes": merged},
        )
        if status != 200:
            print(f"  FAIL  {entity_id:60s}  [PATCH {status}] {body}")
            skipped += 1
            continue
        print(f"  ok    {entity_id:60s}  → {applicable}  ({label})")
        written += 1

    print()
    print(f"Wrote {written} / {len(all_ids)} entities ({skipped} skipped)")
    return 0 if skipped == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
