# ARCHIVED — one-time F5 bootstrap / prose-mining recovery only.
# NOT routine maintenance. Not import-safe from this path without restoring to scripts/cortex/.
#!/usr/bin/env python3
"""Mine cortex agent-skills SOT bodies and extend skill→skill graph (F5).

Extends the Slice A workspace-tree manifest with deterministic references
mined from ``agent-skills/*.md`` SOT bodies. Workspace manifest edges win on
conflict; legacy skill→skill ``related_to`` rows outside the two symmetric
pairs are retired before seeding.

Idempotent: attribute PATCH noop on match; relationship POST dedupes.
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from backfill_agent_skill_related_edges import (  # noqa: E402
    EDGE_MANIFEST,
    RELATED_SKILLS_BY_SOURCE,
    SkillEdge,
    _create_edge,
    _patch_related_skills,
)
from _skill_sot_miner import (  # noqa: E402
    MinedEdge,
    default_sot_root,
    default_workspaces_root,
    mine_all_sot_edges,
    mined_to_edges,
)

_WS = "workspaces://universal-llm-gateway"
_SESSION = "claude-cursor-2026-06-16-sot-mining"
_AGENT = "claude-cursor"

_SYMMETRIC_RELATED_TO = frozenset(
    {
        frozenset({"build-pipeline", "refine-pipeline"}),
        frozenset({"multi-model-review", "review-task-guidance"}),
    }
)


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


def _fetch_agent_skill_slugs(client: object) -> set[str]:
    status, body = _request(client, "GET", "/entities?type=agent_skill&limit=500")
    if status != 200:
        raise RuntimeError(f"GET agent_skill entities failed: {status}")
    slugs: set[str] = set()
    for row in body.get("items", []):
        eid = str(row.get("id") or "")
        if eid.startswith("agent_skill:"):
            slugs.add(eid.removeprefix("agent_skill:"))
    return slugs


def _manifest_key(edge: SkillEdge | MinedEdge) -> tuple[str, str, str]:
    return (edge.source, edge.target, edge.rel_type)


def _merge_edges(
    manifest: tuple[SkillEdge, ...], mined: list[MinedEdge]
) -> list[SkillEdge]:
    merged: dict[tuple[str, str, str], SkillEdge] = {
        _manifest_key(edge): edge for edge in manifest
    }
    for edge in mined:
        key = _manifest_key(edge)
        if key in merged:
            continue
        pair = frozenset({edge.source, edge.target})
        if edge.rel_type == "related_to" and pair not in _SYMMETRIC_RELATED_TO:
            continue
        if any(
            _manifest_key(existing)[:2] == key[:2]
            and existing.rel_type != edge.rel_type
            for existing in merged.values()
        ):
            continue
        merged[key] = SkillEdge(
            edge.source,
            edge.target,
            edge.rel_type,
            edge.strength,
            edge.role,
        )
    return [merged[k] for k in sorted(merged)]


def _related_skills_map(edges: list[SkillEdge]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for edge in edges:
        out.setdefault(edge.source, [])
        if edge.target not in out[edge.source]:
            out[edge.source].append(edge.target)
        if edge.rel_type == "related_to":
            out.setdefault(edge.target, [])
            if edge.source not in out[edge.target]:
                out[edge.target].append(edge.source)
    return out


def _list_skill_link_relationships(client: object) -> list[dict]:
    status, body = _request(
        client,
        "GET",
        "/relationships?type_id=references&limit=500",
    )
    refs = body.get("items", []) if status == 200 else []
    status, body = _request(
        client,
        "GET",
        "/relationships?type_id=related_to&limit=500",
    )
    rels = body.get("items", []) if status == 200 else []
    rows: list[dict] = []
    for row in (*refs, *rels):
        src = str(row.get("source_id") or "")
        tgt = str(row.get("target_id") or "")
        if src.startswith("agent_skill:") and tgt.startswith("agent_skill:"):
            rows.append(row)
    return rows


def _edge_identity(row: dict) -> tuple[str, str, str]:
    return (
        str(row.get("source_id") or "").removeprefix("agent_skill:"),
        str(row.get("target_id") or "").removeprefix("agent_skill:"),
        str(row.get("type_id") or ""),
    )


def _retire_legacy_edges(
    client: object,
    final_edges: list[SkillEdge],
    *,
    dry_run: bool,
) -> int:
    allowed = {_manifest_key(edge) for edge in final_edges}
    failures = 0
    for row in _list_skill_link_relationships(client):
        src, tgt, rel_type = _edge_identity(row)
        key = (src, tgt, rel_type)
        pair = frozenset({src, tgt})
        if key in allowed:
            continue
        if rel_type == "related_to" and pair in _SYMMETRIC_RELATED_TO:
            rev = (tgt, src, rel_type)
            if rev in allowed:
                continue
        rel_id = row.get("id")
        label = f"{src} → {tgt} ({rel_type})"
        if dry_run:
            print(f"  WOULD RETIRE  {label}")
            continue
        code, body = _request(
            client,
            "DELETE",
            f"/relationships/{rel_id}",
        )
        if code != 200:
            print(f"  FAIL RETIRE  {label}  [{code}] {body}", file=sys.stderr)
            failures += 1
        else:
            print(f"  ok RETIRE    {label}")
    return failures


def _filter_edges(edges: list[SkillEdge], valid_slugs: set[str]) -> list[SkillEdge]:
    return [
        edge
        for edge in edges
        if edge.source in valid_slugs and edge.target in valid_slugs
    ]


def _report_merge(
    manifest: tuple[SkillEdge, ...], final_edges: list[SkillEdge], mined_count: int
) -> None:
    manifest_keys = {_manifest_key(e) for e in manifest}
    final_keys = {_manifest_key(e) for e in final_edges}
    added = final_keys - manifest_keys
    print("Merge summary")
    print(f"  Workspace manifest edges : {len(manifest)}")
    print(f"  Mined SOT sources        : {mined_count}")
    print(f"  Final edge count         : {len(final_edges)}")
    print(f"  Net new edges            : {len(added)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--sot-root",
        type=Path,
        default=None,
        help="CORTEX_FILES_ROOT (default: env or /mnt/torus/mcp-data/files)",
    )
    parser.add_argument(
        "--mine-only",
        action="store_true",
        help="Print mined targets per source and exit (no cortex-api writes).",
    )
    args = parser.parse_args(argv)

    sot_root = args.sot_root or default_sot_root()
    ws_root = default_workspaces_root()

    if args.mine_only:
        mined = mine_all_sot_edges(sot_root=sot_root, ws_root=ws_root)
        for source in sorted(mined):
            print(f"{source}: {', '.join(sorted(mined[source]))}")
        print(f"\n{len(mined)} sources, {sum(len(v) for v in mined.values())} edges")
        return 0

    from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402

    try:
        client = make_sync_client(DEFAULT_CORTEX_URL)
    except Exception as exc:
        print(f"ERROR: cortex-api unreachable: {exc}", file=sys.stderr)
        return 2

    valid_targets = _fetch_agent_skill_slugs(client)
    mined_map = mine_all_sot_edges(
        sot_root=sot_root, ws_root=ws_root, valid_targets=valid_targets
    )
    mined_edges = mined_to_edges(mined_map)
    final_edges = _filter_edges(_merge_edges(EDGE_MANIFEST, mined_edges), valid_targets)
    related_map = _related_skills_map(final_edges)

    _report_merge(EDGE_MANIFEST, final_edges, len(mined_map))
    print()

    if args.dry_run:
        print("DRY RUN — no writes will be issued")
        print()

    failures = _retire_legacy_edges(client, final_edges, dry_run=args.dry_run)
    print()

    print(f"Patching related_skills on {len(related_map)} source skills")
    for source_slug in sorted(related_map):
        if not _patch_related_skills(
            client,
            source_slug,
            related_map[source_slug],
            dry_run=args.dry_run,
        ):
            failures += 1

    print()
    print(f"Seeding {len(final_edges)} skill→skill edges")
    for edge in final_edges:
        if not _create_edge(client, edge, dry_run=args.dry_run):
            failures += 1

    if not args.dry_run:
        from cortex_store.dispatch_ops.ops_audit import _op_audit

        audit = _op_audit(
            kinds=["agent_skill_related_skills_no_relationship"], emit=False
        )
        warnings = int(audit.get("warnings") or 0)
        print()
        print(f"Audit agent_skill_related_skills_no_relationship: warnings={warnings}")
        if warnings:
            for finding in audit.get("findings", []):
                print(f"  - {finding.get('subject')}: {finding.get('detail')}")
            failures += warnings

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
