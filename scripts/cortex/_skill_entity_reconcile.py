"""Reconcile catalog slugs against live agent_skill entities and source_uri."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from _skill_constants import _WS
from claude_bundles.catalog import get_skill_catalog

_REPO_DEFAULT = Path(__file__).resolve().parent.parent.parent


def _fetch_active_agent_skill_rows(
    client: object,
) -> tuple[dict[str, dict[str, object]] | None, str | None]:
    from _skill_projection import _entity_get, _request

    status, body = _request(
        client,
        "GET",
        "/entities?type=agent_skill&limit=500&include_non_active=false",
    )
    if status != 200:
        return None, f"cortex entities GET failed: HTTP {status}"
    items = body.get("items") or body.get("entities") or []
    rows: dict[str, dict[str, object]] = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        eid = str(row.get("id") or "")
        if not eid.startswith("agent_skill:"):
            continue
        slug = eid.removeprefix("agent_skill:")
        # List stubs omit source_uri until the serving process selects it.
        # Full entity GET already returns the column on the current server.
        if row.get("source_uri"):
            rows[slug] = row
            continue
        get_status, live = _entity_get(client, eid)
        if get_status == 200 and isinstance(live, dict):
            merged = dict(row)
            merged["source_uri"] = live.get("source_uri")
            rows[slug] = merged
        else:
            rows[slug] = row
    return rows, None


def _catalog_slugs(indexed: Iterable[str] | None) -> list[str]:
    if indexed is not None:
        return sorted(set(indexed))
    return sorted(get_skill_catalog().entries)


def reconcile_indexed_vs_entities(
    indexed: Iterable[str] | None = None,
    *,
    client: object | None = None,
    repo_root: Path | None = None,
) -> tuple[list[str], list[str], str | None]:
    """Return (failure lines, entity_not_indexed, skip_reason).

    ``entity_not_indexed`` stays empty: non-catalog active entities are
    expected. Every catalog slug must have an active entity whose
    ``source_uri`` matches ``resolve_canonical_source_uri`` and whose
    resolved file exists (plugin, ``.cursor/skills``, or ``.claude/skills``).
    """
    del repo_root
    if client is None:
        return [], [], "cortex unavailable"
    rows, err = _fetch_active_agent_skill_rows(client)
    if rows is None:
        return [], [], err
    failures = _source_uri_failures(rows, _catalog_slugs(indexed))
    return failures, [], None


def _source_uri_failures(
    rows: dict[str, dict[str, object]],
    slugs: list[str],
    *,
    repo_root: Path | None = None,
) -> list[str]:
    from implement_admission.skill_catalog_resolver import (
        SkillCatalogResolveError,
        resolve_canonical_source_uri,
    )

    root = (repo_root or _REPO_DEFAULT).resolve()
    failures: list[str] = []
    for slug in slugs:
        row = rows.get(slug)
        if row is None:
            failures.append(f"missing entity: {slug}")
            continue
        live_uri = row.get("source_uri")
        if not live_uri:
            failures.append(f"null source_uri: {slug}")
            continue
        try:
            expected = resolve_canonical_source_uri(slug)
        except SkillCatalogResolveError as exc:
            failures.append(f"resolver error: {slug}: {exc}")
            continue
        except FileNotFoundError:
            failures.append(f"resolved file missing: {slug}")
            continue
        if str(live_uri) != expected:
            failures.append(
                f"source_uri mismatch: {slug} live={live_uri!r} expected={expected!r}"
            )
            continue
        if not expected.startswith(f"{_WS}/"):
            failures.append(f"resolved file missing: {slug} uri={expected}")
            continue
        path = root / expected.removeprefix(f"{_WS}/")
        if not path.is_file():
            failures.append(f"resolved file missing: {slug} path={path}")
    return failures


def run_entity_reconcile_check(
    *,
    client: object | None = None,
    repo_root: Path | None = None,
) -> int:
    """Print reconciliation diff; return 1 on missing entity or source_uri drift."""
    if client is None:
        print("INFO entity-reconcile skipped: cortex unavailable", flush=True)
        return 0
    rows, err = _fetch_active_agent_skill_rows(client)
    if rows is None:
        print(f"INFO entity-reconcile skipped: {err}", flush=True)
        return 0
    slugs = _catalog_slugs(None)
    failures = _source_uri_failures(rows, slugs, repo_root=repo_root)
    if failures:
        print(
            "RECONCILE: catalog source_uri failures: " + "; ".join(failures),
            flush=True,
        )
        return 1
    print(f"OK entity-reconcile {len(slugs)}/{len(slugs)}", flush=True)
    return 0
