"""Pre-dispatch + CI freshness probe for ``config/skills.yaml`` (F1 / AC17)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

from implement_admission.skill_catalog_resolver import (
    RESOLVER_VERSION,
    catalog_digest,
    catalog_source_uris,
    canonical_catalog_slug,
    resolve_canonical_source_uri,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WS_PREFIX = "workspaces://universal-llm-gateway/"


@dataclass(frozen=True, slots=True)
class FreshnessViolation:
    slug: str
    reason: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class FreshnessReport:
    ok: bool
    catalog_digest: str
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
    if uri.startswith(_WS_PREFIX):
        rel = uri.removeprefix(_WS_PREFIX)
        path = _REPO_ROOT / rel
        return path if path.is_file() else None
    if uri.startswith("workspaces://"):
        rel = uri.split("universal-llm-gateway/", 1)[-1]
        path = _REPO_ROOT / rel
        return path if path.is_file() else None
    if uri.startswith("agent-skills/"):
        from os import environ

        cortex_root = Path(
            environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files")
        ).expanduser()
        path = cortex_root / uri
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
    return uris


def _preferred_live_uri(uris: dict[str, str]) -> str | None:
    for eid, uri in uris.items():
        if eid.startswith("rule:"):
            return uri
    return next(iter(uris.values()), None)


def check_catalog_valid() -> FreshnessViolation | None:
    """Validate catalog load + resolved URI map stability."""
    from claude_bundles.catalog import load_skill_catalog

    try:
        load_skill_catalog(validate_sot=True)
    except Exception as exc:
        return FreshnessViolation(
            slug="*",
            reason="catalog_invalid",
            detail=str(exc),
        )
    try:
        catalog_source_uris()
    except Exception as exc:
        return FreshnessViolation(
            slug="*",
            reason="catalog_uri_resolution_failed",
            detail=str(exc),
        )
    return None


def check_live_drift(*, cortex_url: str = DEFAULT_CORTEX_URL) -> FreshnessReport:
    """Compare catalog-resolved bodies against live Cortex entities."""
    from claude_bundles.catalog import get_skill_catalog

    violations: list[FreshnessViolation] = []
    valid = check_catalog_valid()
    if valid is not None:
        violations.append(valid)

    catalog = get_skill_catalog()
    try:
        with make_sync_client(cortex_url, timeout=15.0) as client:
            for slug in catalog.slugs():
                committed_uri = resolve_canonical_source_uri(slug)
                committed_digest = _body_digest(committed_uri)
                if committed_digest is None:
                    violations.append(
                        FreshnessViolation(
                            slug=slug,
                            reason="body_missing",
                            detail=f"unresolvable body for {committed_uri!r}",
                        )
                    )
                    continue
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
                live_digest = _body_digest(live_uri) if live_uri else None
                if live_digest != committed_digest:
                    violations.append(
                        FreshnessViolation(
                            slug=slug,
                            reason="body_digest_drift",
                            detail=(
                                f"catalog={committed_uri!r} live={live_uri!r} "
                                f"catalog_digest={committed_digest} live_digest={live_digest}"
                            ),
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
        catalog_digest=catalog_digest(),
        violations=tuple(violations),
    )


def assert_fresh_or_raise(*, cortex_url: str = DEFAULT_CORTEX_URL) -> None:
    """Fail-loud pre-dispatch gate (F1)."""
    report = check_live_drift(cortex_url=cortex_url)
    if report.ok:
        return
    lines = [f"skill catalog freshness failed (resolver={RESOLVER_VERSION}):"]
    for v in report.violations:
        lines.append(
            f"  - {v.slug}: {v.reason}" + (f" ({v.detail})" if v.detail else "")
        )
    raise RuntimeError("\n".join(lines))


def validate_generation_invariants(
    entries: dict[str, str],
    *,
    aliases: dict[str, str] | None = None,
) -> list[str]:
    """Alias / canonical-key checks for synthetic URI maps (tests only)."""
    errors: list[str] = []
    if aliases is None:
        return ["aliases required when validating generation invariants"]
    reverse: dict[str, str] = {}
    for alias, canonical in aliases.items():
        if alias in entries and canonical in entries:
            if entries[alias] != entries[canonical]:
                errors.append(
                    f"alias {alias!r} → {canonical!r} divergent source_uri: "
                    f"{entries[alias]!r} vs {entries[canonical]!r}"
                )
        elif alias in entries and canonical not in entries:
            errors.append(f"alias {alias!r} → {canonical!r} missing canonical row")
        if canonical in reverse and reverse[canonical] != alias:
            errors.append(f"canonical key collision on {canonical!r}")
        reverse[canonical] = alias
    keys = {canonical_catalog_slug(k) for k in entries}
    if len(keys) != len(entries):
        errors.append("canonical-key collision in entries")
    return errors
