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
import sys
import urllib.parse

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

PARTITION: dict[str, list[str]] = {
    "*": [
        "agent_skill:architecture-invariants",
        "agent_skill:boot-execution-discipline",
        "agent_skill:case-evidence-retrieval",
        "agent_skill:cortex-entity-restructure",
        "agent_skill:cortex-orientation",
        "agent_skill:cortex-provenance-discipline",
        "agent_skill:cortex-v24-implementation-arc",
        "agent_skill:document-ocr",
        "agent_skill:docx-ingestion",
        "agent_skill:email-bridge-mailbox",
        "agent_skill:engagement-stance",
        "agent_skill:enrichment-quality-discipline",
        "agent_skill:entity-creation-discipline",
        "agent_skill:entity-lifecycle-discipline",
        "agent_skill:financial-reasoning",
        "agent_skill:friction-review",
        "agent_skill:frontier-model-instructions",
        "agent_skill:grok-web-dispatch",
        "agent_skill:image-video-generation",
        "agent_skill:jupiter-browser-via-mcp",
        "agent_skill:lawyer-stance",
        "agent_skill:legal-opinion-corpus-ingestion",
        "agent_skill:markdown-navigation",
        "agent_skill:mcp-tool-loop-trace-matrix",
        "agent_skill:named-entity-verification-gate",
        "agent_skill:no-silent-inference",
        "agent_skill:pre-deploy-gate-discipline",
        "agent_skill:pipeline-substrate-capabilities",
        "agent_skill:prose-discipline",
        "agent_skill:review-protocol-mandatory-chronology-verification",
        "agent_skill:session-close",
        "agent_skill:session-close-audit",
        "agent_skill:session-close-handoff",
        "agent_skill:session-close-reflective-journal",
        "agent_skill:session-close-transcript",
        "agent_skill:skill-document-writing",
        "agent_skill:thirdparty-api-mirror",
        # Case-specific skills — applicable across both seats since cases are
        # worked from whichever seat is convenient. Previously web-only.
        "agent_skill:boe19p-appeal-discipline",
        "agent_skill:chase-escrow-discipline",
        "agent_skill:chase-escrow-statement-ingestion",
        "agent_skill:claudeburst-shadow-ops",
        "agent_skill:crypto-trading-research",
        "agent_skill:document-lifecycle-tracking",
        "agent_skill:hei-application-discipline",
        "agent_skill:tax",
        "agent_skill:w2-ingestion",
        "agent_skill:xai-mcp-calling-shape",
        # Reconciled 2026-05-29 (todo:agent-skill-applicability-partition-
        # reconciliation): active, seat-agnostic skills previously unpartitioned.
        "agent_skill:implementation-plan-workflow",
        "agent_skill:frontier-reasoning-discipline",
        "agent_skill:subgraph-render",
        "agent_skill:lead-seat-boot",
        "agent_skill:srm",
        "agent_skill:auditor-validatable-confidence",
        "agent_skill:corpus-cross-reference-discipline",
        "agent_skill:agent-bus-discipline",
        "agent_skill:agent-build",
        "agent_skill:evidence-review-discipline",
        "agent_skill:dispatch-workflow",
        "agent_skill:dispatch-shape",
        "agent_skill:consult-routing",
        "agent_skill:completion-provenance-discipline",
        "agent_skill:advisor-timing",
        "agent_skill:agent-identity-signoff",
        "agent_skill:modularize-discipline",
        "agent_skill:provenance-granularity",
        # Partitioned here for backfill membership; OVERRIDDEN below to its true
        # multi-agent value ['claude-cursor', 'claude-web'] (not universal).
        "agent_skill:mcp-surface-change",
        # Moved from the claude-cursor bucket (thread 1264): web-lead ULG work has
        # no IDE *_ws.mdc backstop, so claude-web needs ulg-architecture in its
        # manifest. Partitioned here for backfill membership; OVERRIDDEN below to
        # ['claude-cursor', 'claude-web'].
        "agent_skill:ulg-architecture",
        # Partitioned here for backfill membership; OVERRIDDEN below to lead
        # seats ['claude-web', 'claude-cursor', 'grok-direct'] (not universal).
        "agent_skill:consensus-steelman-posture",
    ],
    "claude-cursor": [
        # Reconciled 2026-05-29 (direct-verify caught it): cursor-workspace skill
        # (.cursor/skills/); live applicable_agents=['claude-cursor'], not universal.
        "agent_skill:delegate-to-grok",
    ],
    "claude-web": [
        "agent_skill:implement-todo",
        "agent_skill:mode-b-web-orchestrator",
    ],
}

# Harness/archived skills — excluded from boot via lifecycle=deprecated (boot SQL
# uses lifecycle_not_value_sql_predicate). Run with --deprecate-retired to apply.
RETIRED_BOOT_SKILLS: tuple[str, ...] = (
    "agent_skill:grokbuild",
    "agent_skill:grokbuild-v1",
    "agent_skill:grokbuild-v2",
    "agent_skill:grok-build-dispatch",
    "agent_skill:claude-web-boot",
)


# Multi-agent assignments. Wins over the bucket-derived value above.
OVERRIDES: dict[str, list[str]] = {
    # Reconciled 2026-05-29 (direct-verify): cursor+web, not universal; matches live attr.
    "agent_skill:mcp-surface-change": ["claude-cursor", "claude-web"],
    # Thread 1264: web-lead ULG orientation backstop — add claude-web alongside the
    # existing cursor partition (no IDE *_ws.mdc auto-load on the web seat).
    "agent_skill:ulg-architecture": ["claude-cursor", "claude-web"],
    "agent_skill:agent-build": [
        "claude-web",
        "claude-cursor",
        "grok-direct",
        "gpt-cursor",
        "subagent",
    ],
    "agent_skill:grok-web-dispatch": ["grok-web", "claude-web", "claude-cursor"],
    "agent_skill:xai-mcp-calling-shape": [
        "grok-web",
        "claude-web",
        "claude-cursor",
    ],
    # Lead-seat posture (thread 1189) — not universal; applies to lead seats only.
    "agent_skill:consensus-steelman-posture": [
        "claude-web",
        "claude-cursor",
        "grok-direct",
    ],
}


def _request(
    client: object, method: str, path: str, body: dict | None = None
) -> tuple[int, dict]:
    """Issue an HTTP request via the shared httpx client; return (status, body)."""
    import httpx

    assert isinstance(client, httpx.Client)
    kwargs: dict = {}
    if body is not None:
        kwargs["json"] = body
    resp = client.request(method, path, **kwargs)
    try:
        data = resp.json()
    except Exception:
        data = {}
    return resp.status_code, data


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
    label = {
        "*": "universal",
        "claude-web": "web-only",
        "claude-cursor": "cursor-only",
    }.get(slug, slug)
    return applicable, label


def _audit(client: object) -> int:
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
    status, body = _request(client, "GET", "/entities?type=agent_skill&limit=500")
    if status != 200:
        print(f"AUDIT FAIL: GET /entities {status} → {body}")
        return 2
    live_summaries = body.get("items", [])
    # Deprecated/superseded skills will never be partitioned; counting them as
    # "unpartitioned" keeps --audit permanently red (the always-red-detector
    # bug). Exclude them from the active universe. They remain as entities
    # (tombstones) for supersession provenance — out of scope for partitioning.
    excluded_deprecated = sorted(
        row["id"] for row in live_summaries if row.get("lifecycle") == "deprecated"
    )
    live_ids = {
        row["id"] for row in live_summaries if row.get("lifecycle") != "deprecated"
    }
    partitioned = set(OVERRIDES.keys()) | {
        eid for ids in PARTITION.values() for eid in ids
    }

    unpartitioned = sorted(live_ids - partitioned)
    orphan = sorted(partitioned - live_ids)

    drifted: list[tuple[str, list[str] | None, list[str]]] = []
    for entity_id in sorted(partitioned & live_ids):
        expected, _label = _applicable_for(entity_id)
        status, body = _request(
            client, "GET", f"/entities/{urllib.parse.quote(entity_id, safe=':')}"
        )
        if status != 200:
            continue
        attrs = body.get("attributes") or {}
        actual = attrs.get("applicable_agents") if isinstance(attrs, dict) else None
        if actual != expected:
            drifted.append((entity_id, actual, expected))

    print("Audit: agent_skill applicability")
    print(f"  Active skills total     : {len(live_ids)}")
    print(f"  Excluded (deprecated)   : {len(excluded_deprecated)}")
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


def _deprecate_retired(client: object, *, dry_run: bool) -> int:
    """Set lifecycle=deprecated on RETIRED_BOOT_SKILLS entities still live."""
    failures = 0
    print(f"Deprecating {len(RETIRED_BOOT_SKILLS)} retired boot skill(s)")
    if dry_run:
        print("DRY RUN — no writes will be issued")
    print()
    for entity_id in RETIRED_BOOT_SKILLS:
        status, body = _request(
            client,
            "GET",
            f"/entities/{urllib.parse.quote(entity_id, safe=':')}",
        )
        if status == 404:
            print(f"  SKIP  {entity_id:60s}  [not found]")
            continue
        if status != 200:
            print(f"  FAIL  {entity_id:60s}  [GET {status}]")
            failures += 1
            continue
        if body.get("lifecycle") == "deprecated":
            print(f"  noop  {entity_id:60s}  (already deprecated)")
            continue
        if dry_run:
            print(f"  WOULD {entity_id:60s}  → lifecycle=deprecated")
            continue
        status, body = _request(
            client,
            "PATCH",
            f"/entities/{urllib.parse.quote(entity_id, safe=':')}",
            body={"lifecycle": "deprecated"},
        )
        if status != 200:
            print(f"  FAIL  {entity_id:60s}  [PATCH {status}] {body}")
            failures += 1
        else:
            print(f"  OK    {entity_id:60s}  → deprecated")
    return failures


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
    parser.add_argument(
        "--deprecate-retired",
        action="store_true",
        help=(
            "Set lifecycle=deprecated on RETIRED_BOOT_SKILLS (grokbuild*, "
            "claude-web-boot). Composes with the applicability backfill."
        ),
    )
    args = parser.parse_args()

    client = make_sync_client(DEFAULT_CORTEX_URL)

    exit_code = 0
    if args.deprecate_retired:
        exit_code = max(exit_code, _deprecate_retired(client, dry_run=args.dry_run))

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

        status, body = _request(
            client,
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
            print(f"  WOULD {entity_id:60s}  → {applicable}  ({label})  prior={prior}")
            continue

        status, body = _request(
            client,
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
    backfill_code = 0 if skipped == 0 else 1
    return max(exit_code, backfill_code)


if __name__ == "__main__":
    sys.exit(main())
