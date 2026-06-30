"""Cortex entity projection, comparison, and upsert helpers."""

from __future__ import annotations

import sys
import urllib.parse

from _skill_constants import _SUPPRESSED, slug_to_name
from _skill_scan import _create_lifecycle


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
        "name": slug_to_name(slug),
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
