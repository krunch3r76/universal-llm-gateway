"""Manifest read/write/compare for generated ``.cursor/skills/`` stubs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from _skill_audit import _PARITY_ALLOWLIST
from _skill_constants import (
    _SUPPRESSED,
    GENERATOR_VERSION,
    MANIFEST_FILENAME,
    normalize_slug,
)
from _skill_projection import _entity_get, _request
from _skill_render import extract_renderer_fields, renderer_field_values
from _skill_scan import _scan_skills

VerdictStatus = Literal["clean", "dirty", "error", "warnings"]


def _entity_slug(entity_id: str) -> str:
    return entity_id.split(":", 1)[1] if ":" in entity_id else entity_id


def manifest_path(repo_root: Path) -> Path:
    return repo_root / ".cursor" / "skills" / MANIFEST_FILENAME


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def compute_allowlist_hash(allowlist: dict[str, dict[str, str]]) -> str:
    payload = [
        [slug, {k: meta[k] for k in sorted(meta)}]
        for slug, meta in sorted(allowlist.items(), key=lambda item: item[0])
    ]
    digest = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
    return f"sha256:{digest}"


def compute_output_tree_hash(skills_dir: Path) -> str:
    pairs: list[list[str]] = []
    if skills_dir.is_dir():
        for path in sorted(skills_dir.glob("*/SKILL.md")):
            rel = str(path.relative_to(skills_dir))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            pairs.append([rel, digest])
    tree_digest = hashlib.sha256(_canonical_json(pairs).encode()).hexdigest()
    return f"sha256:{tree_digest}"


def compute_sot_snapshot_hash(entries: list[tuple[str, dict[str, Any]]]) -> str:
    payload: list[list[Any]] = []
    for slug, fields in sorted(entries, key=lambda item: item[0]):
        values = renderer_field_values(fields)
        payload.append([slug, list(values)])
    digest = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
    return f"sha256:{digest}"


def _discoverable(entity: dict[str, Any]) -> bool:
    lifecycle = entity.get("lifecycle")
    if lifecycle in _SUPPRESSED:
        return False
    if lifecycle not in (None, "active"):
        return False
    if entity.get("discoverable") is False:
        return False
    return True


def fetch_discoverable_entities(client: object) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entity_type in ("agent_skill", "rule", "skill"):
        status, body = _request(
            client, "GET", f"/entities?type={entity_type}&limit=500"
        )
        if status != 200:
            raise RuntimeError(f"GET /entities?type={entity_type} failed: {status}")
        for row in body.get("items", []):
            entity_id = str(row.get("id") or "")
            if ":" not in entity_id:
                continue
            slug = normalize_slug(_entity_slug(entity_id))
            get_status, live = _entity_get(client, entity_id)
            if get_status != 200 or not _discoverable(live):
                continue
            out[slug] = live
    return out


def build_renderer_snapshot(
    client: object,
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, dict[str, Any]]]:
    entities = fetch_discoverable_entities(client)
    entries = [
        (slug, extract_renderer_fields(entities[slug], slug))
        for slug in sorted(entities)
    ]
    return entries, entities


def read_manifest(repo_root: Path) -> dict[str, Any] | None:
    path = manifest_path(repo_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_manifest(repo_root: Path, payload: dict[str, Any]) -> None:
    path = manifest_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = _canonical_json(payload) + "\n"
    path.write_text(encoded, encoding="utf-8")


def build_manifest_payload(
    *,
    repo_root: Path,
    client: object,
    generated_count: int,
    skipped_allowlist: list[str],
) -> dict[str, Any]:
    entries, _ = build_renderer_snapshot(client)
    skills_dir = repo_root / ".cursor" / "skills"
    return {
        "sot_snapshot_hash": compute_sot_snapshot_hash(entries),
        "generator_version": GENERATOR_VERSION,
        "allowlist_hash": compute_allowlist_hash(_PARITY_ALLOWLIST),
        "generated_count": generated_count,
        "skipped_allowlist": sorted(skipped_allowlist),
        "output_tree_hash": compute_output_tree_hash(skills_dir),
        "checked_at": datetime.now(UTC).isoformat(),
    }


def verify_manifest(
    repo_root: Path,
    client: object,
) -> tuple[VerdictStatus, list[str]]:
    """Importable manifest preflight — dirty/error on any mismatch."""
    stored = read_manifest(repo_root)
    if stored is None:
        return "error", ["missing .generated-manifest.json"]
    entries, _ = build_renderer_snapshot(client)
    skills_dir = repo_root / ".cursor" / "skills"
    current = {
        "sot_snapshot_hash": compute_sot_snapshot_hash(entries),
        "generator_version": GENERATOR_VERSION,
        "allowlist_hash": compute_allowlist_hash(_PARITY_ALLOWLIST),
        "output_tree_hash": compute_output_tree_hash(skills_dir),
    }
    problems = [
        f"{key} mismatch"
        for key in (
            "sot_snapshot_hash",
            "generator_version",
            "allowlist_hash",
            "output_tree_hash",
        )
        if stored.get(key) != current[key]
    ]
    if problems:
        return "dirty", problems
    return "clean", []


def generator_manifest_verdict(
    repo_root: Path,
    client: object,
) -> tuple[VerdictStatus, list[str]]:
    return verify_manifest(repo_root, client)


def aggregate_verdicts(
    *,
    edge_drift: VerdictStatus,
    stub_parity: VerdictStatus,
    generator_manifest: VerdictStatus,
    allowlist: VerdictStatus,
) -> dict[str, VerdictStatus]:
    return {
        "EDGE_DRIFT": edge_drift,
        "STUB_PARITY": stub_parity,
        "GENERATOR_MANIFEST": generator_manifest,
        "ALLOWLIST": allowlist,
    }


def scanned_stub_slugs(repo_root: Path) -> dict[str, dict[str, object]]:
    return _scan_skills(repo_root.resolve())
