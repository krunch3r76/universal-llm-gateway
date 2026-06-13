#!/usr/bin/env python3
"""Upsert rule entity projections from docs/agent-guides/rules/*.md manifest.

Mirrors scripts/cortex/backfill_agent_skill_applicability.py: writes via the
cortex-api HTTP client (not manual sqlite3). Each rule entity is a digest-bound
projection — source_uri + digest + applicability only; git/generated file is
body SoT.

Usage:
    scripts/gen-rules --target agent-guides-rules   # emit files first
    scripts/gen-rules --target cortex-rules         # upsert projections
    scripts/gen-rules --target cortex-rules --check # drift gate (CI fail-closed)
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from cortex_store.routes._skill_index import content_digest  # noqa: E402
from gen_rules.agent_guides import (  # noqa: E402
    AGENT_GUIDES_RULE_SLUGS,
    normalize_rule_entry,
    validate_rule_manifest_slugs,
)
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402


def _request(
    client: object, method: str, path: str, body: dict | None = None
) -> tuple[int, dict]:
    kwargs: dict = {}
    if body is not None:
        kwargs["json"] = body
    resp = client.request(method, path, **kwargs)
    try:
        data = resp.json()
    except Exception:
        data = {}
    return resp.status_code, data


def _rule_source_uri(slug: str) -> str:
    return f"docs/agent-guides/rules/{slug}.md"


def _projection_for(root: Path, slug: str) -> dict[str, object] | None:
    entry = normalize_rule_entry(AGENT_GUIDES_RULE_SLUGS[slug])
    source_uri = _rule_source_uri(slug)
    rule_path = root / source_uri
    if not rule_path.is_file():
        print(f"ERROR: missing generated rule file: {rule_path}", file=sys.stderr)
        return None
    try:
        digest = content_digest(rule_path.read_bytes())
    except OSError:
        print(f"ERROR: digest unavailable for {slug}", file=sys.stderr)
        return None
    attrs: dict[str, object] = {
        "applicable_agents": entry["applicable_agents"],
        "digest": digest,
        "delivery_priority": entry["delivery_priority"],
    }
    if entry["capabilities_required"]:
        attrs["capabilities_required"] = entry["capabilities_required"]
    return {
        "id": f"rule:{slug}",
        "type": "rule",
        "name": slug,
        "source_uri": source_uri,
        "attributes": attrs,
    }


def _fetch_entity(client: object, entity_id: str) -> tuple[int, dict]:
    return _request(
        client,
        "GET",
        f"/entities/{urllib.parse.quote(entity_id, safe=':')}",
    )


def _upsert(client: object, projection: dict[str, object], *, dry_run: bool) -> bool:
    entity_id = str(projection["id"])
    status, body = _fetch_entity(client, entity_id)
    if status not in (200, 404):
        print(f"  FAIL  {entity_id:40s}  [GET {status}] {body}", file=sys.stderr)
        return False
    if dry_run:
        action = "WOULD PATCH" if status == 200 else "WOULD CREATE"
        print(f"  {action}  {entity_id}")
        return True
    if status == 404:
        status, body = _request(client, "POST", "/entities", body=projection)
        label = "CREATE"
    else:
        status, body = _request(
            client,
            "PATCH",
            f"/entities/{urllib.parse.quote(entity_id, safe=':')}",
            body={
                "source_uri": projection["source_uri"],
                "attributes": projection["attributes"],
            },
        )
        label = "PATCH"
    if status not in (200, 201):
        print(f"  FAIL  {entity_id:40s}  [{label} {status}] {body}", file=sys.stderr)
        return False
    print(f"  ok    {entity_id:40s}  [{label}]")
    return True


def _projection_matches_live(
    live: dict, expected: dict[str, object]
) -> tuple[bool, str]:
    attrs = live.get("attributes") or {}
    if not isinstance(attrs, dict):
        return False, "attributes not a dict"
    exp_attrs = expected["attributes"]
    assert isinstance(exp_attrs, dict)
    if live.get("source_uri") != expected["source_uri"]:
        return False, f"source_uri live={live.get('source_uri')!r}"
    if attrs.get("digest") != exp_attrs.get("digest"):
        return (
            False,
            f"digest live={attrs.get('digest')!r} expected={exp_attrs.get('digest')!r}",
        )
    if attrs.get("applicable_agents") != exp_attrs.get("applicable_agents"):
        return (
            False,
            f"applicable_agents live={attrs.get('applicable_agents')!r} "
            f"expected={exp_attrs.get('applicable_agents')!r}",
        )
    live_caps = attrs.get("capabilities_required", [])
    exp_caps = exp_attrs.get("capabilities_required", [])
    if live_caps != exp_caps:
        return False, f"capabilities_required live={live_caps!r} expected={exp_caps!r}"
    if attrs.get("delivery_priority") != exp_attrs.get("delivery_priority"):
        return (
            False,
            f"delivery_priority live={attrs.get('delivery_priority')!r} "
            f"expected={exp_attrs.get('delivery_priority')!r}",
        )
    return True, ""


def _check(client: object, root: Path) -> int:
    failures = 0
    for slug in sorted(AGENT_GUIDES_RULE_SLUGS):
        projection = _projection_for(root, slug)
        if projection is None:
            failures += 1
            continue
        entity_id = str(projection["id"])
        status, live = _fetch_entity(client, entity_id)
        if status == 404:
            print(f"DRIFT: {entity_id} missing from cortex", file=sys.stderr)
            failures += 1
            continue
        if status != 200:
            print(f"DRIFT: {entity_id} GET {status} → {live}", file=sys.stderr)
            failures += 1
            continue
        ok, reason = _projection_matches_live(live, projection)
        if not ok:
            print(f"DRIFT: {entity_id} {reason}", file=sys.stderr)
            failures += 1
    return failures


def _audit(client: object, root: Path) -> int:
    status, body = _request(client, "GET", "/entities?type=rule&limit=500")
    if status != 200:
        print(f"AUDIT FAIL: GET /entities?type=rule {status} → {body}", file=sys.stderr)
        return 2
    live_ids = {row["id"] for row in body.get("items", [])}
    expected_ids = {f"rule:{slug}" for slug in AGENT_GUIDES_RULE_SLUGS}
    missing = sorted(expected_ids - live_ids)
    orphan = sorted(live_ids - expected_ids)
    drifted: list[str] = []
    for slug in sorted(AGENT_GUIDES_RULE_SLUGS):
        projection = _projection_for(root, slug)
        if projection is None:
            continue
        entity_id = str(projection["id"])
        st, live = _fetch_entity(client, entity_id)
        if st != 200:
            continue
        ok, reason = _projection_matches_live(live, projection)
        if not ok:
            drifted.append(f"{entity_id}: {reason}")
    print("Audit: rule projections")
    print(f"  Expected manifest slugs : {len(expected_ids)}")
    print(f"  Live rule entities      : {len(live_ids)}")
    print(f"  Missing projections     : {len(missing)}")
    for eid in missing:
        print(f"    - {eid}")
    print(f"  Drifted projections     : {len(drifted)}")
    for line in drifted:
        print(f"    - {line}")
    print(f"  Orphan rule entities    : {len(orphan)}")
    for eid in orphan:
        print(f"    - {eid}")
    return 0 if not (missing or drifted) else 1


def main(argv: list[str] | None = None) -> int:
    validate_rule_manifest_slugs()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify stored digest + applicability match current generated files.",
    )
    parser.add_argument("--audit", action="store_true", help="Read-only drift report.")
    parser.add_argument(
        "--root",
        type=Path,
        default=_REPO,
        help="Workspace root containing docs/agent-guides/rules/",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()

    try:
        client = make_sync_client(DEFAULT_CORTEX_URL)
    except Exception as exc:
        print(f"ERROR: cortex-api unreachable: {exc}", file=sys.stderr)
        return 2

    if args.audit:
        return _audit(client, root)
    if args.check:
        failures = _check(client, root)
        if failures:
            print(f"CHECK FAIL: {failures} drift(s)", file=sys.stderr)
            return 1
        print("OK cortex-rules --check")
        return 0

    failures = 0
    for slug in sorted(AGENT_GUIDES_RULE_SLUGS):
        projection = _projection_for(root, slug)
        if projection is None:
            failures += 1
            continue
        if not _upsert(client, projection, dry_run=args.dry_run):
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
