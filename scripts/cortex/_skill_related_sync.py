"""Sync ``related_skills`` attributes and ``references`` edges from declared lists."""

from __future__ import annotations

import sys
import urllib.parse

INVARIANT_TARGETS = frozenset({"architecture-invariants", "ulg-architecture"})
_REMEDIATION = "python scripts/cortex/ingest_skills.py"


def remediation_hint() -> str:
    return _REMEDIATION


_SOT_SYNC_ATTR_KEYS = ("trigger_match_terms", "trigger_short", "skill_category")


def patch_sot_skill_attrs(
    client: object,
    slug: str,
    attrs: dict[str, object],
    *,
    dry_run: bool,
) -> bool:
    """Merge cortex SOT frontmatter fields onto an existing agent_skill entity."""
    patch = {k: attrs[k] for k in _SOT_SYNC_ATTR_KEYS if k in attrs}
    if not patch:
        return True
    entity_id = f"agent_skill:{slug}"
    q = urllib.parse.quote(entity_id, safe=":")
    resp = client.request("GET", f"/entities/{q}")
    if resp.status_code != 200:
        print(f"  FAIL  {entity_id:40s}  [GET {resp.status_code}]", file=sys.stderr)
        return False
    live = resp.json()
    merged = dict(live.get("attributes") or {})
    if all(merged.get(k) == patch.get(k) for k in patch):
        print(f"  noop  {entity_id:40s}  sot attrs")
        return True
    merged.update(patch)
    if dry_run:
        print(f"  WOULD PATCH {entity_id:40s}  sot attrs={patch}")
        return True
    resp = client.request("PATCH", f"/entities/{q}", json={"attributes": merged})
    if resp.status_code != 200:
        print(
            f"  FAIL  {entity_id:40s}  [PATCH {resp.status_code}] {resp.text}",
            file=sys.stderr,
        )
        return False
    print(f"  ok    {entity_id:40s}  sot attrs={patch}")
    return True


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
        json={"attributes": merged},
    )
    if resp.status_code != 200:
        print(
            f"  FAIL  {entity_id:40s}  [PATCH {resp.status_code}] {resp.text}",
            file=sys.stderr,
        )
        return False
    print(f"  ok    {entity_id:40s}  related_skills={slugs}")
    return True


def _request(client: object, method: str, path: str) -> tuple[int, dict]:
    resp = client.request(method, path)
    try:
        data = resp.json()
    except Exception:
        data = {}
    return resp.status_code, data


def list_outgoing_reference_edges(client: object, source_slug: str) -> list[dict]:
    """Active ``references`` edges from ``agent_skill:<source_slug>`` (outgoing only)."""
    entity_id = f"agent_skill:{source_slug}"
    q = urllib.parse.quote(entity_id, safe=":")
    status, body = _request(
        client,
        "GET",
        f"/relationships?entity_id={q}&type_id=references&limit=500",
    )
    if status != 200:
        return []
    rows: list[dict] = []
    for row in body.get("items", []):
        if str(row.get("source_id") or "") == entity_id:
            rows.append(row)
    return rows


def _reference_target_slug(row: dict) -> str | None:
    target_id = str(row.get("target_id") or "")
    if not target_id.startswith("agent_skill:"):
        return None
    return target_id.removeprefix("agent_skill:")


def prune_stale_reference_edges(
    client: object,
    source_slug: str,
    declared: list[str],
    *,
    dry_run: bool,
    session_id: str = "ingest-skills-related-sync",
    agent: str = "ingest-skills",
) -> bool:
    """Soft-delete stale ``references`` edges not present in ``declared``.

    Never touches ``related_to`` rows or ``references`` whose target is not
    ``agent_skill:*``.
    """
    _ = session_id, agent
    declared_set = set(declared)
    ok = True
    for row in list_outgoing_reference_edges(client, source_slug):
        target_slug = _reference_target_slug(row)
        if target_slug is None or target_slug in declared_set:
            continue
        rel_id = row.get("id")
        label = f"{source_slug} → {target_slug} (references)"
        if dry_run:
            print(f"  WOULD-RETIRE  {label}")
            continue
        status, body = _request(client, "DELETE", f"/relationships/{rel_id}")
        if status != 200:
            print(
                f"  FAIL  RETIRE {label:50s}  [{status}] {body}",
                file=sys.stderr,
            )
            ok = False
        else:
            print(f"  ok    RETIRE {label:50s}")
    return ok


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
        json={
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
    if not prune_stale_reference_edges(
        client,
        source_slug,
        targets,
        dry_run=dry_run,
        session_id=session_id,
        agent=agent,
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
    if not prune_stale_reference_edges(
        client,
        source_slug,
        declared,
        dry_run=dry_run,
        session_id=session_id,
        agent=agent,
    ):
        ok = False
    return ok
