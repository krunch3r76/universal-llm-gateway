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
import os
import re
import sys
import urllib.parse
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from _skill_related_parse import (  # noqa: E402
    BARE_SLUG_RE,
    declared_related_skills,
    parse_frontmatter,
)
from _skill_graph_report import build_drift_report  # noqa: E402
from _skill_related_sync import (  # noqa: E402
    list_outgoing_reference_edges,
    patch_sot_skill_attrs,
    remediation_hint,
    sync_declared_related,
    sync_reference_edges_only,
)
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402

_CANONICAL_DOC_RE = re.compile(
    r"universal-llm-gateway/docs/agent-guides/skills/([A-Za-z0-9_-]+)\.md"
)
_CORTEX_SOT_RE = re.compile(
    r"SOT:\*{0,2}\s*`?cortex://agent-skills/([A-Za-z0-9_-]+)\.md`?"
)
_SUPPRESSED = frozenset({"deprecated", "retired"})
_CREATE_SUPPRESSED_LIFECYCLES = frozenset({"deprecated", "retired", "merged"})
_WS = "workspaces://universal-llm-gateway"
_SYNC_SOURCE_URI = f"{_WS}/docs/agent-guides/skills/skill-document-writing.md"
_SKIP_CORTEX_SOT = frozenset({"README"})


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
    return _request(client, "GET", f"/entities/{q}")


def _cortex_files_root() -> Path:
    return Path(
        os.environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files")
    ).expanduser()


def _scan_cortex_sot_metadata() -> dict[str, dict[str, object]]:
    """Declared frontmatter from cortex ``agent-skills/*.md`` (not workspace stubs)."""
    skills_dir = _cortex_files_root() / "agent-skills"
    if not skills_dir.is_dir():
        return {}
    found: dict[str, dict[str, object]] = {}
    for path in sorted(skills_dir.glob("*.md")):
        slug = path.stem
        if slug in _SKIP_CORTEX_SOT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            print(f"ERROR: unreadable {path}", file=sys.stderr)
            continue
        fm = parse_frontmatter(text)
        entry: dict[str, object] = {}
        declared = declared_related_skills(text, fm)
        if isinstance(fm.get("related_skills"), list):
            entry["related_skills"] = declared
        elif declared:
            entry["related_skills"] = declared
        for key in ("trigger_match_terms", "trigger_short", "skill_category"):
            if key in fm:
                entry[key] = fm[key]
        if entry:
            found[slug] = entry
    return found


def _scan_cortex_sot_declared() -> dict[str, list[str]]:
    meta = _scan_cortex_sot_metadata()
    return {
        slug: list(meta["related_skills"])
        for slug, meta in meta.items()
        if isinstance(meta.get("related_skills"), list)
    }


def _parse_frontmatter(text: str) -> dict[str, object]:
    return parse_frontmatter(text)


def _declared_related_skills(text: str, fm: dict[str, object]) -> list[str]:
    return declared_related_skills(text, fm)


def _create_lifecycle(fm: dict[str, object]) -> str:
    """Default discoverable lifecycle on CREATE when source frontmatter is non-suppressed."""
    raw = fm.get("lifecycle")
    if isinstance(raw, str):
        lc = raw.strip().lower()
        if lc in _CREATE_SUPPRESSED_LIFECYCLES:
            return lc
    return "active"


def _source_uri(slug: str, body: str, root: Path) -> str:
    sot = _CORTEX_SOT_RE.search(body)
    if sot:
        return f"agent-skills/{sot.group(1)}.md"
    match = _CANONICAL_DOC_RE.search(body)
    if match and match.group(1) == slug:
        return f"{_WS}/docs/agent-guides/skills/{slug}.md"
    subdir_skill = root / "docs" / "agent-guides" / "skills" / slug / "SKILL.md"
    if subdir_skill.is_file():
        return f"{_WS}/docs/agent-guides/skills/{slug}/SKILL.md"
    return f"{_WS}/.cursor/skills/{slug}/SKILL.md"


def _scan_skills(root: Path) -> dict[str, dict[str, object]]:
    skills_dir = root / ".cursor" / "skills"
    if not skills_dir.is_dir():
        print(f"ERROR: missing skills dir: {skills_dir}", file=sys.stderr)
        return {}
    found: dict[str, dict[str, object]] = {}
    for skill_path in sorted(skills_dir.glob("*/SKILL.md")):
        slug = skill_path.parent.name
        try:
            text = skill_path.read_text(encoding="utf-8")
        except OSError:
            print(f"ERROR: unreadable {skill_path}", file=sys.stderr)
            continue
        fm = parse_frontmatter(text)
        description = str(fm.get("description") or "").strip()
        if not description:
            print(f"ERROR: missing description: {skill_path}", file=sys.stderr)
            continue
        found[slug] = {
            "slug": slug,
            "frontmatter": fm,
            "description": description,
            "source_uri": _source_uri(slug, text, root),
            "related_skills": declared_related_skills(text, fm),
        }
    return found


def _projection(
    scanned: dict[str, object], *, live: dict | None = None
) -> dict[str, object]:
    slug = str(scanned["slug"])
    fm = scanned["frontmatter"]
    assert isinstance(fm, dict)
    live_attrs = (live or {}).get("attributes") or {}
    if not isinstance(live_attrs, dict):
        live_attrs = {}
    attrs: dict[str, object] = {}
    if "applicable_agents" in fm:
        attrs["applicable_agents"] = fm["applicable_agents"]
    elif live is None:
        attrs["applicable_agents"] = ["*"]
    else:
        attrs["applicable_agents"] = live_attrs.get("applicable_agents", ["*"])
    for key in ("skill_category", "trigger_short", "trigger_match_terms"):
        if key in fm:
            attrs[key] = fm[key]
    declared = scanned.get("related_skills")
    fm_declared = isinstance(fm.get("related_skills"), list)
    if isinstance(declared, list) and (declared or fm_declared):
        attrs["related_skills"] = declared
    elif live is not None and "related_skills" in live_attrs:
        attrs["related_skills"] = live_attrs["related_skills"]
    result: dict[str, object] = {
        "id": f"agent_skill:{slug}",
        "type": "agent_skill",
        "name": " ".join(p.capitalize() for p in slug.split("-")),
        "description": scanned["description"],
        "source_uri": scanned["source_uri"],
        "attributes": attrs,
    }
    if live is None:
        result["lifecycle"] = _create_lifecycle(fm)
    return result


def _matches(live: dict, expected: dict[str, object]) -> tuple[bool, str]:
    attrs = live.get("attributes") or {}
    exp = expected["attributes"]
    assert isinstance(attrs, dict) and isinstance(exp, dict)
    for field in ("source_uri", "description"):
        if live.get(field) != expected[field]:
            return False, f"{field} live={live.get(field)!r}"
    if attrs.get("applicable_agents") != exp.get("applicable_agents"):
        return False, f"applicable_agents live={attrs.get('applicable_agents')!r}"
    live_related = attrs.get("related_skills")
    exp_related = exp.get("related_skills")
    if live_related is not None or exp_related is not None:
        if sorted(live_related or []) != sorted(exp_related or []):
            return (
                False,
                f"related_skills live={live_related!r} declared={exp_related!r}",
            )
    if "digest" in attrs:
        return False, "digest must not be stored on agent_skill"
    return True, ""


def _upsert(
    client: object,
    projection: dict[str, object],
    *,
    dry_run: bool,
    live: dict | None,
) -> bool:
    entity_id = str(projection["id"])
    q = urllib.parse.quote(entity_id, safe=":")
    if live is None:
        status, live = _entity_get(client, entity_id)
        if status not in (200, 404):
            print(f"  FAIL  {entity_id:40s}  [GET {status}]", file=sys.stderr)
            return False
    else:
        status = 200
    if status == 200 and live.get("lifecycle") in _SUPPRESSED:
        print(f"  SKIP  {entity_id:40s}  (lifecycle={live.get('lifecycle')})")
        return True
    if dry_run:
        print(f"  {'WOULD PATCH' if status == 200 else 'WOULD CREATE'}  {entity_id}")
        return True
    if status == 404:
        code, body = _request(client, "POST", "/entities", body=projection)
        label = "CREATE"
    else:
        merged = dict(live.get("attributes") or {})
        merged.update(projection["attributes"])
        merged.pop("digest", None)
        code, body = _request(
            client,
            "PATCH",
            f"/entities/{q}",
            body={
                "description": projection["description"],
                "source_uri": projection["source_uri"],
                "attributes": merged,
            },
        )
        label = "PATCH"
    if code not in (200, 201):
        print(f"  FAIL  {entity_id:40s}  [{label} {code}] {body}", file=sys.stderr)
        return False
    print(f"  ok    {entity_id:40s}  [{label}]")
    return True


def _expected_declared_related(
    scanned: dict[str, object],
    live: dict | None,
) -> list[str] | None:
    fm = scanned["frontmatter"]
    assert isinstance(fm, dict)
    declared = scanned.get("related_skills")
    if isinstance(declared, list) and (
        declared or isinstance(fm.get("related_skills"), list)
    ):
        return [str(v) for v in declared]
    if live is not None:
        attrs = live.get("attributes") or {}
        if isinstance(attrs, dict) and "related_skills" in attrs:
            live_related = attrs.get("related_skills")
            if isinstance(live_related, list):
                return [str(v) for v in live_related]
    return None


def _reference_edge_drift(
    client: object,
    slug: str,
    declared: list[str],
    live_edges: list[dict] | None = None,
) -> list[str]:
    eid = f"agent_skill:{slug}"
    if live_edges is None:
        live_edges = list_outgoing_reference_edges(client, slug)
    declared_set = set(declared)
    edge_targets: set[str] = set()
    for row in live_edges:
        target_id = str(row.get("target_id") or "")
        if not target_id.startswith("agent_skill:"):
            continue
        edge_targets.add(target_id.removeprefix("agent_skill:"))
    out: list[str] = []
    for target in sorted(declared_set - edge_targets):
        out.append(
            f"{eid} missing references edge to agent_skill:{target} — "
            f"run: {remediation_hint()}"
        )
    for target in sorted(edge_targets - declared_set):
        out.append(
            f"{eid} stale references edge to agent_skill:{target} "
            f"(not in declared list) — run: {remediation_hint()}"
        )
    return out


def _related_skills_drift(
    client: object,
    slug: str,
    declared: list[str],
    live_by_id: dict[str, dict] | None = None,
) -> str | None:
    eid = f"agent_skill:{slug}"
    if live_by_id is None:
        status, live = _entity_get(client, eid)
        if status == 404:
            return f"{eid} missing from cortex"
        if status != 200:
            return f"{eid} GET {status}"
    else:
        live = live_by_id.get(eid)
        if live is None:
            return f"{eid} missing from cortex"
    if live.get("lifecycle") in _SUPPRESSED:
        return None
    attrs = live.get("attributes") or {}
    live_related = attrs.get("related_skills")
    if sorted(live_related or []) != sorted(declared or []):
        return (
            f"{eid} related_skills live={live_related!r} "
            f"declared={declared!r} — run: {remediation_hint()}"
        )
    return None


def _drifts(
    client: object,
    scanned: dict[str, dict[str, object]],
    live_by_id: dict[str, dict] | None = None,
    *,
    cortex_declared: dict[str, list[str]] | None = None,
) -> list[str]:
    out: list[str] = []
    for slug in sorted(scanned):
        eid = f"agent_skill:{slug}"
        if live_by_id is None:
            status, live = _entity_get(client, eid)
            if status == 404:
                out.append(f"{eid} missing from cortex")
                continue
            if status != 200:
                out.append(f"{eid} GET {status}")
                continue
        else:
            live = live_by_id.get(eid)
            if live is None:
                out.append(f"{eid} missing from cortex")
                continue
        if live.get("lifecycle") in _SUPPRESSED:
            continue
        ok, reason = _matches(live, _projection(scanned[slug], live=live))
        if not ok:
            out.append(f"{eid} {reason}")
        expected = _expected_declared_related(scanned[slug], live)
        if expected is not None:
            out.extend(_reference_edge_drift(client, slug, expected))
    if cortex_declared:
        for slug in sorted(cortex_declared):
            if slug in scanned:
                continue
            drift = _related_skills_drift(
                client, slug, cortex_declared[slug], live_by_id
            )
            if drift:
                out.append(drift)
            out.extend(_reference_edge_drift(client, slug, cortex_declared[slug]))
    return out


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


def _audit_terms(client: object, scanned: dict[str, dict[str, object]]) -> int:
    _ = scanned
    status, body = _request(client, "GET", "/entities?type=agent_skill&limit=500")
    if status != 200:
        print(
            f"AUDIT-TERMS FAIL: GET /entities?type=agent_skill {status}",
            file=sys.stderr,
        )
        return 2
    empty: list[str] = []
    for stub in body.get("items", []):
        entity_id = str(stub.get("id") or "")
        if not entity_id.startswith("agent_skill:"):
            continue
        get_status, live = _entity_get(client, entity_id)
        if get_status != 200:
            print(
                f"AUDIT-TERMS FAIL: GET /entities/{entity_id} {get_status}",
                file=sys.stderr,
            )
            return 2
        if live.get("lifecycle") in _SUPPRESSED:
            continue
        attrs = live.get("attributes") or {}
        terms = attrs.get("trigger_match_terms") if isinstance(attrs, dict) else None
        if not isinstance(terms, list) or not terms:
            empty.append(entity_id)
    print(
        f"Audit-terms: {len(empty)} active agent_skill(s) with empty trigger_match_terms"
    )
    for eid in sorted(empty):
        print(f"  - {eid}")
    return 0 if not empty else 1


def _audit(client: object, scanned: dict[str, dict[str, object]], root: Path) -> int:
    status, body = _request(client, "GET", "/entities?type=agent_skill&limit=500")
    if status != 200:
        print(f"AUDIT FAIL: GET /entities?type=agent_skill {status}", file=sys.stderr)
        return 2
    live_by_id = {row["id"]: row for row in body.get("items", [])}
    cortex_declared = _scan_cortex_sot_declared()
    drifted = _drifts(client, scanned, live_by_id, cortex_declared=cortex_declared)
    file_gone = [
        eid
        for eid, row in live_by_id.items()
        if eid not in {f"agent_skill:{s}" for s in scanned}
        and row.get("lifecycle") not in _SUPPRESSED
        and str(row.get("source_uri") or "").startswith(f"{_WS}/.cursor/skills/")
        and not (root / str(row["source_uri"]).removeprefix(f"{_WS}/")).is_file()
    ]
    print("Audit: agent_skill filesystem projections")
    print(f"  Scanned workspace skills : {len(scanned)}")
    print(f"  Drifted projections      : {len(drifted)}")
    for line in drifted:
        print(f"    - {line}")
    print(f"  File-gone (report only)  : {len(file_gone)}")
    for eid in sorted(file_gone):
        print(f"    - {eid}")
    return 0 if not drifted else 1


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
