#!/usr/bin/env python3
"""Upsert agent_skill projections from workspace + cortex SOT declared fields.

Workspace: ``.cursor/skills/*/SKILL.md`` (description, applicable_agents, …).
Cortex SOT: ``$CORTEX_FILES_ROOT/agent-skills/*.md`` declared ``related_skills`` only.

Steady-state companion graph sync (attribute + ``references`` edges) is **always**
``python scripts/cortex/ingest_skills.py`` after editing a declared companion list.
The prose miner is archived at
``scripts/cortex/archive/bootstrap_skill_sot_prose_miner.py`` (one-time F5 bootstrap /
prose-mining recovery only — not routine maintenance). Recovery from prose-only refs:
declare them in the SOT ``related_skills`` list, then re-run this script.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from _skill_audit import _audit, _audit_parity, _audit_terms  # noqa: E402
from _skill_constants import _SUPPRESSED, _SYNC_SOURCE_URI  # noqa: E402
from _skill_drift import _drifts, _reference_edge_drift  # noqa: E402
from _skill_graph_report import build_drift_report  # noqa: E402
from _skill_projection import _entity_get, _projection, _upsert  # noqa: E402
from _skill_related_parse import BARE_SLUG_RE  # noqa: E402
from _skill_related_sync import (  # noqa: E402
    patch_sot_skill_attrs,
    remediation_hint,
    sync_declared_related,
    sync_reference_edges_only,
)
from _skill_scan import (  # noqa: E402
    _scan_cortex_sot_declared,
    _scan_cortex_sot_metadata,
    _scan_skills,
)
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402


def _resolve_slug(
    client: object,
    slug: str,
    scanned: dict[str, dict[str, object]],
    cortex_meta: dict[str, dict[str, object]],
) -> str | None:
    if slug in scanned or slug in cortex_meta:
        return slug
    status, live = _entity_get(client, f"agent_skill:{slug}")
    if status == 200 and live.get("lifecycle") not in _SUPPRESSED:
        return slug
    return None


def _filter_for_slug(
    slug: str,
    scanned: dict[str, dict[str, object]],
    cortex_meta: dict[str, dict[str, object]],
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    ws = {slug: scanned[slug]} if slug in scanned else {}
    cortex = {slug: cortex_meta[slug]} if slug in cortex_meta else {}
    return ws, cortex


def _sync_live_only_skill(
    client: object,
    slug: str,
    *,
    dry_run: bool,
) -> bool:
    status, live = _entity_get(client, f"agent_skill:{slug}")
    if status != 200:
        print(f"  FAIL  agent_skill:{slug:40s}  [GET {status}]", file=sys.stderr)
        return False
    if live.get("lifecycle") in _SUPPRESSED:
        print(f"  SKIP  agent_skill:{slug:40s}  (lifecycle={live.get('lifecycle')})")
        return True
    attrs = live.get("attributes") or {}
    declared = attrs.get("related_skills")
    if not isinstance(declared, list):
        declared = []
    return sync_reference_edges_only(
        client,
        slug,
        [str(v) for v in declared],
        dry_run=dry_run,
        source_uri=_SYNC_SOURCE_URI,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--report",
        action="store_true",
        help="With --check, emit structured drift metrics as JSON on stdout",
    )
    parser.add_argument("--audit", action="store_true")
    parser.add_argument(
        "--audit-terms",
        action="store_true",
        help="Exit 1 if any active agent_skill has empty trigger_match_terms",
    )
    parser.add_argument(
        "--audit-parity",
        action="store_true",
        help=(
            "Report cross-surface skill parity gaps (cortex SOT body vs .cursor stub); "
            "report-only, always exit 0"
        ),
    )
    parser.add_argument(
        "--slug",
        type=str,
        default=None,
        help="Reconcile only this skill slug (workspace, cortex SOT, or live entity)",
    )
    parser.add_argument("--root", type=Path, default=_REPO)
    args = parser.parse_args(argv)

    if args.slug and not BARE_SLUG_RE.match(args.slug):
        print(f"ERROR: invalid slug {args.slug!r}", file=sys.stderr)
        return 2

    scanned = _scan_skills(args.root.resolve())
    cortex_meta = _scan_cortex_sot_metadata()
    cortex_declared = _scan_cortex_sot_declared()

    try:
        client = make_sync_client(DEFAULT_CORTEX_URL)
    except Exception as exc:
        print(f"ERROR: cortex-api unreachable: {exc}", file=sys.stderr)
        return 2

    slug_filter = args.slug
    if slug_filter:
        if _resolve_slug(client, slug_filter, scanned, cortex_meta) is None:
            print(f"ERROR: unknown skill slug {slug_filter!r}", file=sys.stderr)
            return 2
        scanned, cortex_meta = _filter_for_slug(slug_filter, scanned, cortex_meta)
        cortex_declared = (
            {slug_filter: cortex_declared[slug_filter]}
            if slug_filter in cortex_declared
            else {}
        )

    if not slug_filter and not scanned:
        return 2

    if args.report and not args.check:
        print("ERROR: --report requires --check", file=sys.stderr)
        return 2

    if args.audit:
        return _audit(client, scanned, args.root.resolve())

    if args.audit_terms:
        return _audit_terms(client, scanned)

    if args.audit_parity:
        lines = _audit_parity(scanned)
        print(f"Parity: {len(lines)} cross-surface gap(s)")
        for line in lines:
            print(f"  - {line}")
        return 0

    if args.check:
        drifted = _drifts(client, scanned, cortex_declared=cortex_declared)
        if slug_filter and not scanned and slug_filter not in cortex_declared:
            status, live = _entity_get(client, f"agent_skill:{slug_filter}")
            if status == 200 and live.get("lifecycle") not in _SUPPRESSED:
                attrs = live.get("attributes") or {}
                declared = attrs.get("related_skills")
                if isinstance(declared, list):
                    drifted.extend(
                        _reference_edge_drift(
                            client,
                            slug_filter,
                            [str(v) for v in declared],
                        )
                    )
        report = build_drift_report(drifted)
        if args.report:
            print(json.dumps(report, sort_keys=True))
        if drifted:
            for line in drifted:
                print(f"DRIFT: {line}", file=sys.stderr)
            print(
                f"CHECK FAIL: {len(drifted)} drift(s) — fix declared lists then "
                f"{remediation_hint()}",
                file=sys.stderr,
            )
            return 1
        print("OK ingest_skills --check")
        return 0

    if slug_filter:
        print(f"Ingesting skill slug: {slug_filter}")
    else:
        print(f"Ingesting {len(scanned)} workspace skills")
    if cortex_declared:
        print(f"Cortex SOT declared related_skills: {len(cortex_declared)} skill(s)")
    if cortex_meta:
        print(f"Cortex SOT metadata sync: {len(cortex_meta)} skill(s)")
    if args.dry_run:
        print("DRY RUN — no writes will be issued")
    print()
    failures = 0
    for slug in sorted(scanned):
        eid = f"agent_skill:{slug}"
        status, live = _entity_get(client, eid)
        if status not in (200, 404):
            print(f"  FAIL  {eid:40s}  [GET {status}]", file=sys.stderr)
            failures += 1
            continue
        live_body = live if status == 200 else None
        entry = scanned[slug]
        if not _upsert(
            client,
            _projection(entry, live=live_body),
            dry_run=args.dry_run,
            live=live_body,
        ):
            failures += 1
            continue
        declared = entry.get("related_skills")
        fm = entry["frontmatter"]
        assert isinstance(fm, dict)
        if isinstance(declared, list) and (
            declared or isinstance(fm.get("related_skills"), list)
        ):
            sync_list = [str(v) for v in declared]
            if not sync_reference_edges_only(
                client,
                slug,
                sync_list,
                dry_run=args.dry_run,
                source_uri=_SYNC_SOURCE_URI,
            ):
                failures += 1
    for slug in sorted(cortex_meta):
        meta = cortex_meta[slug]
        declared = meta.get("related_skills")
        if slug not in scanned and isinstance(declared, list):
            if not sync_declared_related(
                client,
                slug,
                [str(v) for v in declared],
                dry_run=args.dry_run,
                source_uri=_SYNC_SOURCE_URI,
            ):
                failures += 1
        attr_patch = {
            k: meta[k]
            for k in ("trigger_match_terms", "trigger_short", "skill_category")
            if k in meta
        }
        if attr_patch and not patch_sot_skill_attrs(
            client, slug, attr_patch, dry_run=args.dry_run
        ):
            failures += 1
    if slug_filter and not scanned and not cortex_meta:
        if not _sync_live_only_skill(client, slug_filter, dry_run=args.dry_run):
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
