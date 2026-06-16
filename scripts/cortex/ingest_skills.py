#!/usr/bin/env python3
"""Upsert agent_skill projections from .cursor/skills/*/SKILL.md (no stored digest)."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_RELATED_SKILLS_SECTION_RE = re.compile(
    r"^## Related skills\s*\n((?:[-*]\s+[a-z0-9-]+\s*\n)+)",
    re.MULTILINE,
)
_BARE_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
_CANONICAL_DOC_RE = re.compile(
    r"universal-llm-gateway/docs/agent-guides/skills/([A-Za-z0-9_-]+)\.md"
)
_SUPPRESSED = frozenset({"deprecated", "retired"})
_WS = "workspaces://universal-llm-gateway"


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


def _parse_frontmatter(text: str) -> dict[str, object]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    data: dict[str, object] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key, raw = key.strip(), raw.strip()
        if not key:
            continue
        if key == "applicable_agents":
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                data[key] = [str(v) for v in parsed]
            continue
        if key == "related_skills":
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                data[key] = [str(v).split("#", 1)[0].strip() for v in parsed]
            continue
        if (raw.startswith('"') and raw.endswith('"')) or (
            raw.startswith("'") and raw.endswith("'")
        ):
            raw = raw[1:-1]
        data[key] = raw
    return data


def _parse_related_skills_section(text: str) -> list[str]:
    match = _RELATED_SKILLS_SECTION_RE.search(text)
    if not match:
        return []
    slugs: list[str] = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line.startswith(("-", "*")):
            continue
        slug = line.lstrip("-*").strip().split("#", 1)[0].strip()
        if slug.startswith("agent_skill:"):
            slug = slug.removeprefix("agent_skill:")
        if _BARE_SLUG_RE.match(slug) and slug not in slugs:
            slugs.append(slug)
    return slugs


def _declared_related_skills(text: str, fm: dict[str, object]) -> list[str]:
    from_fm = fm.get("related_skills")
    if isinstance(from_fm, list) and from_fm:
        return [str(v) for v in from_fm]
    return _parse_related_skills_section(text)


def _source_uri(slug: str, body: str) -> str:
    match = _CANONICAL_DOC_RE.search(body)
    if match:
        doc = match.group(1)
        return f"{_WS}/docs/agent-guides/skills/{doc}.md"
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
        fm = _parse_frontmatter(text)
        description = str(fm.get("description") or "").strip()
        if not description:
            print(f"ERROR: missing description: {skill_path}", file=sys.stderr)
            continue
        found[slug] = {
            "slug": slug,
            "frontmatter": fm,
            "description": description,
            "source_uri": _source_uri(slug, text),
            "related_skills": _declared_related_skills(text, fm),
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
    for key in ("skill_category", "trigger_short"):
        if key in fm:
            attrs[key] = fm[key]
    declared = scanned.get("related_skills")
    fm_declared = isinstance(fm.get("related_skills"), list)
    if isinstance(declared, list) and (declared or fm_declared):
        attrs["related_skills"] = declared
    elif live is not None and "related_skills" in live_attrs:
        attrs["related_skills"] = live_attrs["related_skills"]
    return {
        "id": f"agent_skill:{slug}",
        "type": "agent_skill",
        "name": " ".join(p.capitalize() for p in slug.split("-")),
        "description": scanned["description"],
        "source_uri": scanned["source_uri"],
        "attributes": attrs,
    }


def _matches(live: dict, expected: dict[str, object]) -> tuple[bool, str]:
    attrs = live.get("attributes") or {}
    exp = expected["attributes"]
    assert isinstance(attrs, dict) and isinstance(exp, dict)
    for field in ("source_uri", "description"):
        if live.get(field) != expected[field]:
            return False, f"{field} live={live.get(field)!r}"
    if attrs.get("applicable_agents") != exp.get("applicable_agents"):
        return False, f"applicable_agents live={attrs.get('applicable_agents')!r}"
    if attrs.get("related_skills") != exp.get("related_skills"):
        return False, f"related_skills live={attrs.get('related_skills')!r}"
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


def _drifts(
    client: object,
    scanned: dict[str, dict[str, object]],
    live_by_id: dict[str, dict] | None = None,
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
    return out


def _audit(client: object, scanned: dict[str, dict[str, object]], root: Path) -> int:
    status, body = _request(client, "GET", "/entities?type=agent_skill&limit=500")
    if status != 200:
        print(f"AUDIT FAIL: GET /entities?type=agent_skill {status}", file=sys.stderr)
        return 2
    live_by_id = {row["id"]: row for row in body.get("items", [])}
    drifted = _drifts(client, scanned, live_by_id)
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
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--root", type=Path, default=_REPO)
    args = parser.parse_args(argv)
    scanned = _scan_skills(args.root.resolve())
    if not scanned:
        return 2
    try:
        client = make_sync_client(DEFAULT_CORTEX_URL)
    except Exception as exc:
        print(f"ERROR: cortex-api unreachable: {exc}", file=sys.stderr)
        return 2
    if args.audit:
        return _audit(client, scanned, args.root.resolve())
    if args.check:
        drifted = _drifts(client, scanned)
        if drifted:
            for line in drifted:
                print(f"DRIFT: {line}", file=sys.stderr)
            print(f"CHECK FAIL: {len(drifted)} drift(s)", file=sys.stderr)
            return 1
        print("OK ingest_skills --check")
        return 0
    print(f"Ingesting {len(scanned)} workspace skills")
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
        if not _upsert(
            client,
            _projection(scanned[slug], live=live_body),
            dry_run=args.dry_run,
            live=live_body,
        ):
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
