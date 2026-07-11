#!/usr/bin/env python3
"""Backfill guidance_class + export_surfaces on skill-pool-dedupe demotes/matter.

Authority: skill-pool-dedupe judgment (agent-bus:4891) + skill-guidance-policy.md.
Idempotent read-modify-write; preserves unrelated attributes.
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

MATTER_RETIRING: tuple[str, ...] = (
    "agent_skill:tax",
    "agent_skill:w2-ingestion",
    "agent_skill:legal-opinion-corpus-ingestion",
    "agent_skill:crypto-trading-research",
    "agent_skill:case-evidence-retrieval",
    "agent_skill:lawyer-stance",
    "agent_skill:psych-framework-counsel",
)

CURSOR_ONLY: tuple[str, ...] = (
    "agent_skill:add-mcp-tool",
    "agent_skill:agent-bus-multitask",
    "agent_skill:build-pipeline",
    "agent_skill:corpus-cross-reference-discipline",
    "agent_skill:corpus-map-authoring",
    "agent_skill:cursor-rule-authoring",
    "agent_skill:cursor-sdk-instruction-standard",
    "agent_skill:debug-with-events",
    "agent_skill:descriptor-authoring-discipline",
    "agent_skill:document-ingestion",
    "agent_skill:document-lifecycle-tracking",
    "agent_skill:docx-ingestion",
    "agent_skill:image-video-generation",
    "agent_skill:implement-work-item",
    "agent_skill:lead-agent-git-integration",
    "agent_skill:mcp-surface-change",
    "agent_skill:mcp-tool-loop-trace-matrix",
    "agent_skill:pipeline-substrate-capabilities",
    "agent_skill:produce-uml",
    "agent_skill:provenance-granularity",
    "agent_skill:rag-canonical-reference-reminder",
    "agent_skill:refine-pipeline",
    "agent_skill:research-article-ingest",
    "agent_skill:research-article-search",
    "agent_skill:review-task-guidance",
    "agent_skill:service-lifecycle",
    "agent_skill:subgraph-render",
    "agent_skill:thirdparty-api-mirror",
    "agent_skill:ulg-architecture",
)

TARGETS: dict[str, dict[str, object]] = {
    **{
        eid: {
            "guidance_class": "matter_retiring",
            "sot_location": "workspace",
            "git_policy": "generated_surface",
            "export_surfaces": ["cursor_hardlink"],
        }
        for eid in MATTER_RETIRING
    },
    **{
        eid: {
            "guidance_class": "cursor_only",
            "sot_location": "workspace",
            "git_policy": "generated_surface",
            "export_surfaces": ["cursor_hardlink", "boot_skills"],
        }
        for eid in CURSOR_ONLY
    },
}


def _request(client: object, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
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


def _attrs_match(existing: dict, target: dict[str, object]) -> bool:
    for key, value in target.items():
        if existing.get(key) != value:
            return False
    return True


def run(*, dry_run: bool = False) -> int:
    client = make_sync_client(DEFAULT_CORTEX_URL)
    written = 0
    noop = 0
    skipped = 0

    print(f"Backfilling guidance attrs on {len(TARGETS)} agent_skill entities")
    if dry_run:
        print("DRY RUN — no writes")
    print()

    for entity_id in sorted(TARGETS):
        target = TARGETS[entity_id]
        status, body = _request(
            client,
            "GET",
            f"/entities/{urllib.parse.quote(entity_id, safe=':')}?intent=full",
        )
        if status != 200:
            print(f"  SKIP  {entity_id:60s}  [GET {status}]")
            skipped += 1
            continue

        existing = body.get("attributes") or {}
        if not isinstance(existing, dict):
            existing = {}
        if _attrs_match(existing, target):
            print(f"  noop  {entity_id:60s}  {target['guidance_class']}")
            noop += 1
            continue

        merged = dict(existing)
        merged.update(target)
        label = str(target["guidance_class"])

        if dry_run:
            print(
                f"  WOULD {entity_id:60s}  → {label}  "
                f"export_surfaces={target['export_surfaces']}"
            )
            continue

        status, body = _request(
            client,
            "PATCH",
            f"/entities/{urllib.parse.quote(entity_id, safe=':')}?intent=full",
            body={"attributes": merged},
        )
        if status != 200:
            print(f"  FAIL  {entity_id:60s}  [PATCH {status}] {body}")
            skipped += 1
            continue
        print(f"  ok    {entity_id:60s}  → {label}")
        written += 1

    print()
    print(f"Wrote {written} / noop {noop} / skipped {skipped} / total {len(TARGETS)}")
    return 0 if skipped == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
