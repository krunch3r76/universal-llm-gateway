"""Reconcile catalog cursor-indexed slugs against live agent_skill entities."""

from __future__ import annotations

from collections.abc import Iterable

from claude_bundles.resolver import cursor_indexed_slugs


def _fetch_active_agent_skill_slugs(
    client: object,
) -> tuple[set[str] | None, str | None]:
    from _skill_projection import _request

    status, body = _request(
        client,
        "GET",
        "/entities?type=agent_skill&limit=500&include_non_active=false",
    )
    if status != 200:
        return None, f"cortex entities GET failed: HTTP {status}"
    items = body.get("items") or body.get("entities") or []
    slugs: set[str] = set()
    for row in items:
        eid = str(row.get("id") or "")
        if eid.startswith("agent_skill:"):
            slugs.add(eid.removeprefix("agent_skill:"))
    return slugs, None


def reconcile_indexed_vs_entities(
    indexed: Iterable[str] | None = None,
    *,
    client: object | None = None,
) -> tuple[list[str], list[str], str | None]:
    """Return (indexed_missing_entity, entity_not_indexed, skip_reason).

    ``entity_not_indexed`` is always empty: non-indexed active entities are
    expected (life_local, retired lanes). Catalog membership is the sole
    authority for which slugs must have entities.
    """
    if client is None:
        return [], [], "cortex unavailable"
    entity_slugs, err = _fetch_active_agent_skill_slugs(client)
    if entity_slugs is None:
        return [], [], err
    indexed_set = set(indexed or cursor_indexed_slugs())
    indexed_missing = sorted(slug for slug in indexed_set if slug not in entity_slugs)
    return indexed_missing, [], None


def run_entity_reconcile_check(*, client: object | None = None) -> int:
    """Print reconciliation diff; return 1 on unexpected mismatches."""
    indexed_missing, _entity_not_indexed, skip = reconcile_indexed_vs_entities(
        client=client
    )
    if skip:
        print(f"INFO entity-reconcile skipped: {skip}", flush=True)
        return 0
    fail = 0
    if indexed_missing:
        print(
            "RECONCILE: indexed slugs missing agent_skill entity: "
            + ", ".join(indexed_missing),
            flush=True,
        )
        fail = 1
    if fail == 0:
        print("OK entity-reconcile", flush=True)
    return fail
