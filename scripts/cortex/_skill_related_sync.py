"""Sync ``related_skills`` attributes and ``references`` edges from declared lists."""

from __future__ import annotations

import sys
import urllib.parse

INVARIANT_TARGETS = frozenset({"architecture-invariants", "ulg-architecture"})
_REMEDIATION = "python scripts/cortex/ingest_skills.py"


def remediation_hint() -> str:
    return _REMEDIATION


def infer_role(target: str) -> str:
    if target in INVARIANT_TARGETS:
        return "invariant"
    return "sot_pointer"


def patch_related_skills(
    client: object,
    source_slug: str,
    slugs: list[str],
    *,
    dry_run: bool,
) -> bool:
    entity_id = f"agent_skill:{source_slug}"
    q = urllib.parse.quote(entity_id, safe=":")
    resp = client.request("GET", f"/entities/{q}")
    if resp.status_code != 200:
        print(f"  FAIL  {entity_id:40s}  [GET {resp.status_code}]", file=sys.stderr)
        return False
    live = resp.json()
    attrs = dict(live.get("attributes") or {})
    if attrs.get("related_skills") == slugs:
        print(f"  noop  {entity_id:40s}  related_skills={slugs}")
        return True
    merged = dict(attrs)
    merged["related_skills"] = slugs
    if dry_run:
        print(f"  WOULD PATCH {entity_id:40s}  related_skills={slugs}")
        return True
    resp = client.request(
        "PATCH",
        f"/entities/{q}",
        body={"attributes": merged},
    )
    if resp.status_code != 200:
        print(
            f"  FAIL  {entity_id:40s}  [PATCH {resp.status_code}] {resp.text}",
            file=sys.stderr,
        )
        return False
    print(f"  ok    {entity_id:40s}  related_skills={slugs}")
    return True


def create_reference_edge(
    client: object,
    source_slug: str,
    target_slug: str,
    *,
    dry_run: bool,
    session_id: str,
    agent: str,
    source_uri: str,
) -> bool:
    label = f"{source_slug} → {target_slug} (references)"
    if dry_run:
        print(f"  WOULD EDGE  {label}")
        return True
    resp = client.request(
        "POST",
        "/relationships",
        body={
            "source_id": f"agent_skill:{source_slug}",
            "target_id": f"agent_skill:{target_slug}",
            "type_id": "references",
            "role": infer_role(target_slug),
            "strength": 0.6,
            "session_id": session_id,
            "agent": agent,
            "source_uri": source_uri,
        },
    )
    if resp.status_code not in (200, 201):
        print(
            f"  FAIL  {label:50s}  [{resp.status_code}] {resp.text}",
            file=sys.stderr,
        )
        return False
    was_new = resp.json().get("was_new", True)
    tag = "CREATE" if was_new else "EXISTS"
    print(f"  ok    {label:50s}  [{tag}]")
    return True


def sync_reference_edges_only(
    client: object,
    source_slug: str,
    targets: list[str],
    *,
    dry_run: bool,
    session_id: str = "ingest-skills-related-sync",
    agent: str = "ingest-skills",
    source_uri: str = "workspaces://universal-llm-gateway/docs/agent-guides/skills/skill-document-writing.md",
) -> bool:
    ok = True
    for target in targets:
        if not create_reference_edge(
            client,
            source_slug,
            target,
            dry_run=dry_run,
            session_id=session_id,
            agent=agent,
            source_uri=source_uri,
        ):
            ok = False
    return ok


def sync_declared_related(
    client: object,
    source_slug: str,
    declared: list[str],
    *,
    dry_run: bool,
    session_id: str = "ingest-skills-related-sync",
    agent: str = "ingest-skills",
    source_uri: str = "workspaces://universal-llm-gateway/docs/agent-guides/skills/skill-document-writing.md",
) -> bool:
    ok = patch_related_skills(client, source_slug, declared, dry_run=dry_run)
    for target in declared:
        if not create_reference_edge(
            client,
            source_slug,
            target,
            dry_run=dry_run,
            session_id=session_id,
            agent=agent,
            source_uri=source_uri,
        ):
            ok = False
    return ok
