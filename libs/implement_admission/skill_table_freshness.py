"""Pre-dispatch + CI freshness probe for the committed skill source table (F1 / AC17)."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

from implement_admission.skill_source_table import (
    CANONICAL_SKILL_SOURCE_URIS,
    CANONICAL_SLUG_ALIASES,
    TABLE_DIGEST,
    TEMPLATE_VERSION,
    canonical_table_key,
    table_bytes_for_digest,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORTEX_FILES_ROOT = Path(
    os.environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files")
).expanduser()
_WS_PREFIX = "workspaces://universal-llm-gateway/"


@dataclass(frozen=True, slots=True)
class FreshnessViolation:
    slug: str
    reason: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class FreshnessReport:
    ok: bool
    table_digest: str
    violations: tuple[FreshnessViolation, ...]


def _entity_source_uri(entity: dict[str, Any]) -> str | None:
    top = entity.get("source_uri")
    if top and str(top).strip():
        return str(top).strip()
    attrs = entity.get("attributes") or {}
    if isinstance(attrs, dict):
        raw = attrs.get("source_uri")
        if raw:
            return str(raw).strip()
    return None


def _resolve_body_path(source_uri: str) -> Path | None:
    uri = source_uri.strip()
    if uri.startswith("agent-skills/"):
        path = _CORTEX_FILES_ROOT / uri
        return path if path.is_file() else None
    if uri.startswith(_WS_PREFIX):
        rel = uri.removeprefix(_WS_PREFIX)
        path = _REPO_ROOT / rel
        return path if path.is_file() else None
    if uri.startswith("workspaces://"):
        rel = uri.split("universal-llm-gateway/", 1)[-1]
        path = _REPO_ROOT / rel
        return path if path.is_file() else None
    return None


def _body_digest(source_uri: str) -> str | None:
    path = _resolve_body_path(source_uri)
    if path is None:
        return None
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _live_entity_uris(client: Any, slug: str) -> dict[str, str]:
    uris: dict[str, str] = {}
    for prefix in ("rule", "agent_skill"):
        eid = f"{prefix}:{slug}"
        resp = client.get(f"/entities/{eid}?intent=full")
        if resp.status_code != 200:
            continue
        payload = resp.json()
        if not isinstance(payload, dict):
            continue
        uri = _entity_source_uri(payload)
        if uri:
            uris[eid] = uri
    if slug == "ulg-architecture_ulg":
        resp = client.get("/entities/agent_skill:ulg-architecture?intent=full")
        if resp.status_code == 200 and isinstance(resp.json(), dict):
            uri = _entity_source_uri(resp.json())
            if uri:
                uris["agent_skill:ulg-architecture"] = uri
    return uris


def _preferred_live_uri(uris: dict[str, str]) -> str | None:
    for eid, uri in uris.items():
        if eid.startswith("rule:"):
            return uri
    return next(iter(uris.values()), None)


def check_table_digest() -> FreshnessViolation | None:
    computed = "sha256:" + hashlib.sha256(table_bytes_for_digest()).hexdigest()
    if computed != TABLE_DIGEST:
        return FreshnessViolation(
            slug="*",
            reason="table_digest_mismatch",
            detail=f"committed={TABLE_DIGEST} computed={computed}",
        )
    return None


def check_live_drift(*, cortex_url: str = DEFAULT_CORTEX_URL) -> FreshnessReport:
    """Compare committed table + body digests against live Cortex entities."""
    violations: list[FreshnessViolation] = []
    digest_v = check_table_digest()
    if digest_v is not None:
        violations.append(digest_v)

    try:
        with make_sync_client(cortex_url, timeout=15.0) as client:
            for slug, committed_uri in CANONICAL_SKILL_SOURCE_URIS.items():
                live_map = _live_entity_uris(client, slug)
                if not live_map:
                    violations.append(
                        FreshnessViolation(
                            slug=slug,
                            reason="entity_missing",
                            detail="no rule:/agent_skill: entity",
                        )
                    )
                    continue
                live_uri = _preferred_live_uri(live_map)
                if live_uri != committed_uri:
                    violations.append(
                        FreshnessViolation(
                            slug=slug,
                            reason="source_uri_drift",
                            detail=f"committed={committed_uri!r} live={live_uri!r}",
                        )
                    )
                body_digest = _body_digest(committed_uri)
                if body_digest is None:
                    violations.append(
                        FreshnessViolation(
                            slug=slug,
                            reason="body_missing",
                            detail=f"unresolvable body for {committed_uri!r}",
                        )
                    )
                distinct_live = set(live_map.values())
                if len(distinct_live) > 1:
                    violations.append(
                        FreshnessViolation(
                            slug=slug,
                            reason="alias_source_uri_divergence",
                            detail=str(live_map),
                        )
                    )
    except Exception as exc:
        violations.append(
            FreshnessViolation(
                slug="*",
                reason="cortex_unreachable",
                detail=str(exc),
            )
        )

    return FreshnessReport(
        ok=not violations,
        table_digest=TABLE_DIGEST,
        violations=tuple(violations),
    )


def assert_fresh_or_raise(*, cortex_url: str = DEFAULT_CORTEX_URL) -> None:
    """Fail-loud pre-dispatch gate (F1)."""
    report = check_live_drift(cortex_url=cortex_url)
    if report.ok:
        return
    lines = [
        f"skill source table freshness failed (template_version={TEMPLATE_VERSION}):"
    ]
    for v in report.violations:
        lines.append(f"  - {v.slug}: {v.reason}" + (f" ({v.detail})" if v.detail else ""))
    raise RuntimeError("\n".join(lines))


def validate_generation_invariants(
    entries: dict[str, str],
    *,
    aliases: dict[str, str] | None = None,
) -> list[str]:
    """Generation-time checks: canonical-key collision + alias divergence."""
    errors: list[str] = []
    alias_map = aliases if aliases is not None else dict(CANONICAL_SLUG_ALIASES)
    reverse: dict[str, str] = {}
    for alias, canonical in alias_map.items():
        if alias in entries and entries[alias] != entries.get(canonical, entries[alias]):
            errors.append(
                f"alias {alias!r} → {canonical!r} divergent source_uri: "
                f"{entries[alias]!r} vs {entries.get(canonical)!r}"
            )
        if canonical in reverse and reverse[canonical] != alias:
            errors.append(f"canonical key collision on {canonical!r}")
        reverse[canonical] = alias
    keys = {canonical_table_key(k) for k in entries}
    if len(keys) != len(entries):
        errors.append("canonical-key collision in entries")
    return errors
