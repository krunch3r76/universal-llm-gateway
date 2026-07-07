#!/usr/bin/env python3
"""Backfill agent_skill related_skills attributes + skill→skill graph edges.

Seeds the 22-edge workspace-tree manifest from web thread 2011
(cortex://notes/system/threads/2011-cortex-skill-entity-edges-investigation.md).
Uses ``references`` for directional links and ``related_to`` for the two
symmetric sibling pairs. Skips delegate-to-grok → grokbuild (retired).

Idempotent: entity PATCH noop when attributes match; relationship POST is
deduped by cortex-api (existing active row → was_new=false).
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402

_WS = "workspaces://universal-llm-gateway"
_SESSION = "claude-cursor-2026-06-16-skill-edges"
_AGENT = "claude-cursor"


@dataclass(frozen=True)
class SkillEdge:
    source: str
    target: str
    rel_type: str
    strength: float
    role: str | None = None


# 22 active edges — thread 2011 manifest (workspace-tree scope).
EDGE_MANIFEST: tuple[SkillEdge, ...] = (
    SkillEdge(
        "add-mcp-tool", "architecture-invariants", "references", 0.6, "invariant"
    ),
    SkillEdge("add-mcp-tool", "mcp-surface-change", "references", 0.8, "sot_pointer"),
    SkillEdge(
        "agent-bus-multitask", "agent-bus-discipline", "references", 0.9, "load_before"
    ),
    SkillEdge(
        "agent-bus-multitask", "dispatch-workflow", "references", 0.7, "sot_pointer"
    ),
    SkillEdge(
        "agent-bus-multitask",
        "implementation-plan-workflow",
        "references",
        0.6,
        "sot_pointer",
    ),
    SkillEdge(
        "consult-routing",
        "cursor-sdk-instruction-standard",
        "references",
        0.9,
        "load_before",
    ),
    SkillEdge(
        "consult-routing", "architecture-invariants", "references", 0.6, "invariant"
    ),
    SkillEdge("consult-routing", "ulg-architecture", "references", 0.6, "invariant"),
    SkillEdge("consult-routing", "friction-review", "references", 0.5, "companion"),
    SkillEdge(
        "debug-with-events", "ulg-architecture", "references", 0.7, "sot_pointer"
    ),
    SkillEdge(
        "debug-with-events", "architecture-invariants", "references", 0.6, "invariant"
    ),
    SkillEdge(
        "handoff-packet-authoring", "consult-routing", "references", 0.8, "companion"
    ),
    SkillEdge(
        "multi-model-review", "dispatch-workflow", "references", 0.7, "sot_pointer"
    ),
    SkillEdge(
        "multi-model-review",
        "mode-b-web-orchestrator",
        "references",
        0.6,
        "sot_pointer",
    ),
    SkillEdge(
        "multi-model-review",
        "implementation-plan-workflow",
        "references",
        0.6,
        "sot_pointer",
    ),
    SkillEdge(
        "multi-model-review",
        "review-task-guidance",
        "related_to",
        0.8,
        "sibling",
    ),
    SkillEdge("refine-pipeline", "build-pipeline", "related_to", 0.8, "sibling"),
    SkillEdge(
        "research-article-ingest",
        "legal-opinion-corpus-ingestion",
        "references",
        0.6,
        "sot_pointer",
    ),
    SkillEdge(
        "service-lifecycle", "ulg-architecture", "references", 0.7, "sot_pointer"
    ),
    SkillEdge(
        "service-lifecycle", "architecture-invariants", "references", 0.6, "invariant"
    ),
    SkillEdge(
        "ulg-architecture", "architecture-invariants", "references", 0.7, "companion"
    ),
    SkillEdge(
        "web-generate-substrate", "consult-routing", "references", 0.6, "sot_pointer"
    ),
)


def _related_skills_by_source() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for edge in EDGE_MANIFEST:
        out.setdefault(edge.source, [])
        if edge.target not in out[edge.source]:
            out[edge.source].append(edge.target)
    return out


RELATED_SKILLS_BY_SOURCE = _related_skills_by_source()


def _request(
    client: object, method: str, path: str, body: dict | None = None
) -> tuple[int, dict]:
    kwargs: dict = {"json": body} if body is not None else {}
    resp = client.request(method, path, **kwargs)
    try:
        data = resp.json()
    except Exception:
        data = {}
    return resp.status_code, data


def _entity_get(client: object, entity_id: str) -> tuple[int, dict]:
    q = urllib.parse.quote(entity_id, safe=":")
    return _request(client, "GET", f"/entities/{q}?intent=full")


def _patch_related_skills(
    client: object,
    source_slug: str,
    slugs: list[str],
    *,
    dry_run: bool,
) -> bool:
    entity_id = f"agent_skill:{source_slug}"
    status, live = _entity_get(client, entity_id)
    if status != 200:
        print(f"  FAIL  {entity_id:40s}  [GET {status}]", file=sys.stderr)
        return False
    attrs = dict(live.get("attributes") or {})
    if attrs.get("related_skills") == slugs:
        print(f"  noop  {entity_id:40s}  related_skills={slugs}")
        return True
    merged = dict(attrs)
    merged["related_skills"] = slugs
    if dry_run:
        print(f"  WOULD PATCH {entity_id:40s}  related_skills={slugs}")
        return True
    code, body = _request(
        client,
        "PATCH",
        f"/entities/{urllib.parse.quote(entity_id, safe=':')}",
        body={"attributes": merged},
    )
    if code != 200:
        print(f"  FAIL  {entity_id:40s}  [PATCH {code}] {body}", file=sys.stderr)
        return False
    print(f"  ok    {entity_id:40s}  related_skills={slugs}")
    return True


def _create_edge(client: object, edge: SkillEdge, *, dry_run: bool) -> bool:
    source_id = f"agent_skill:{edge.source}"
    target_id = f"agent_skill:{edge.target}"
    label = f"{edge.source} → {edge.target} ({edge.rel_type})"
    if dry_run:
        print(f"  WOULD EDGE  {label}")
        return True
    code, body = _request(
        client,
        "POST",
        "/relationships",
        body={
            "source_id": source_id,
            "target_id": target_id,
            "type_id": edge.rel_type,
            "role": edge.role,
            "strength": edge.strength,
            "session_id": _SESSION,
            "agent": _AGENT,
            "source_uri": f"{_WS}/notes/system/specs/cortex-skill-entity-edges.md",
        },
    )
    if code not in (200, 201):
        print(f"  FAIL  {label:50s}  [{code}] {body}", file=sys.stderr)
        return False
    was_new = body.get("was_new", True)
    tag = "CREATE" if was_new else "EXISTS"
    print(f"  ok    {label:50s}  [{tag}]")
    return True


def _self_check_workspace_skills(root_slugs: set[str]) -> bool:
    outbound_sources = set(RELATED_SKILLS_BY_SOURCE)
    skipped_sources = {"delegate-to-grok"}
    no_outbound = root_slugs - outbound_sources - skipped_sources
    expected_no_outbound = {
        "agent-guidance-writing",
        "architecture-invariants",
        "build-pipeline",
        "cursor-sdk-instruction-standard",
        "dispatch-shape",
        "friction-review",
        "produce-uml",
        "review-task-guidance",
        "skill-suggest-utilization",
    }
    ok = (
        len(root_slugs) == 21
        and len(outbound_sources) == 11
        and no_outbound == expected_no_outbound
    )
    print("Self-check: workspace SKILL.md coverage")
    print(f"  Workspace skills scanned : {len(root_slugs)}")
    print(f"  Outbound edge sources    : {len(outbound_sources)}")
    print(f"  Skipped (retired target) : {len(skipped_sources & root_slugs)}")
    print(f"  No-outbound (referenced) : {len(no_outbound)}")
    if root_slugs - outbound_sources - skipped_sources - expected_no_outbound:
        print(
            "  Unexpected no-outbound   : "
            f"{sorted(root_slugs - outbound_sources - skipped_sources - expected_no_outbound)}"
        )
    if expected_no_outbound - no_outbound:
        print(
            f"  Missing no-outbound      : {sorted(expected_no_outbound - no_outbound)}"
        )
    if len(root_slugs) != 21:
        print(f"  Expected 21 workspace skills, got {len(root_slugs)}")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skills-root",
        type=str,
        default="",
        help="Optional path to .cursor/skills for self-check (repo root parent).",
    )
    args = parser.parse_args(argv)

    if args.skills_root:
        from pathlib import Path

        skills_dir = Path(args.skills_root) / ".cursor" / "skills"
        root_slugs = {p.parent.name for p in skills_dir.glob("*/SKILL.md")}
        if not _self_check_workspace_skills(root_slugs):
            return 2

    try:
        client = make_sync_client(DEFAULT_CORTEX_URL)
    except Exception as exc:
        print(f"ERROR: cortex-api unreachable: {exc}", file=sys.stderr)
        return 2

    print(
        f"Backfilling related_skills on {len(RELATED_SKILLS_BY_SOURCE)} source skills"
    )
    print(f"Seeding {len(EDGE_MANIFEST)} skill→skill edges")
    if args.dry_run:
        print("DRY RUN — no writes will be issued")
    print()

    failures = 0
    for source_slug in sorted(RELATED_SKILLS_BY_SOURCE):
        if not _patch_related_skills(
            client,
            source_slug,
            RELATED_SKILLS_BY_SOURCE[source_slug],
            dry_run=args.dry_run,
        ):
            failures += 1

    print()
    for edge in EDGE_MANIFEST:
        if not _create_edge(client, edge, dry_run=args.dry_run):
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
