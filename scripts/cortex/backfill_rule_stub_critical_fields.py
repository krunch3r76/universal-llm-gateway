#!/usr/bin/env python3
"""Backfill stub-critical metadata on discoverable rule/agent_skill projections.

Fills empty ``description``, ``trigger_match_terms``, and ``paired_rule_pointer``
(from paired ``.mdc`` frontmatter or deterministic derivation) so
``gen_skill_stubs.py --generate`` can proceed.
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from _skill_audit import _missing_stub_critical, stub_critical_field_verdict  # noqa: E402
from _skill_constants import paired_rule_exists, slug_to_name  # noqa: E402
from _skill_manifest import fetch_discoverable_entities  # noqa: E402
from _skill_related_parse import parse_frontmatter  # noqa: E402
from _skill_render import extract_renderer_fields  # noqa: E402
from _skill_terms import (  # noqa: E402
    canonicalize_trigger_match_terms,
    derive_trigger_match_terms,
)
from claude_bundles.bundle_description import MAX_SKILL_DESCRIPTION_LEN  # noqa: E402
from gen_rules.agent_guides import AGENT_GUIDES_RULE_SLUGS, normalize_rule_entry  # noqa: E402
from gen_rules.parser import parse_source  # noqa: E402
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402

_SUPPRESSED = frozenset({"deprecated", "retired", "merged"})


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
    return _request(client, "GET", f"/entities/{q}?intent=full")


def _truncate_description(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= MAX_SKILL_DESCRIPTION_LEN:
        return cleaned
    cut = cleaned[: MAX_SKILL_DESCRIPTION_LEN - 1].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:") + "…"


def _find_rule_mdc(slug: str, repo_root: Path) -> Path | None:
    candidates = (
        repo_root.parent / ".cursor" / "rules" / f"{slug}.mdc",
        repo_root / ".cursor" / "rules" / f"{slug}_ws.mdc",
        repo_root / "cursor-plugins" / "ulg-ecosystem" / "rules" / f"{slug}_ulg.mdc",
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _workspace_skill_path(slug: str, repo_root: Path) -> Path | None:
    path = repo_root / ".cursor" / "skills" / slug / "SKILL.md"
    return path if path.is_file() else None


def _description_from_markdown_text(text: str) -> str:
    fm = parse_frontmatter(text)
    desc = str(fm.get("description") or "").strip()
    if desc:
        return _truncate_description(desc)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("description:"):
            raw = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            if raw:
                return _truncate_description(raw)
    body_lines: list[str] = []
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("<!--"):
            in_block = True
            continue
        if in_block:
            if stripped.endswith("-->"):
                in_block = False
            continue
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return _truncate_description(title)
        if stripped.startswith("**") and stripped.endswith("**"):
            return _truncate_description(stripped.strip("*"))
        if "**Invariant" in stripped or stripped.startswith("Invariant"):
            return _truncate_description(stripped.strip("*"))
        if stripped and not stripped.startswith("|"):
            body_lines.append(stripped)
            if len(body_lines) >= 2:
                break
    if body_lines:
        return _truncate_description(" ".join(body_lines))
    return ""


def _agent_surface_source_path(slug: str, repo_root: Path) -> Path | None:
    entry = AGENT_GUIDES_RULE_SLUGS.get(slug)
    if entry is None:
        return None
    normalized = normalize_rule_entry(entry)
    source = str(normalized.get("source") or "").strip()
    if not source:
        return None
    path = repo_root / source
    return path if path.is_file() else None


def _description_from_agent_surface(slug: str, repo_root: Path) -> str:
    source_path = _agent_surface_source_path(slug, repo_root)
    if source_path is not None:
        parsed = parse_source(source_path)
        if parsed.frontmatter_cursor:
            desc = _description_from_markdown_text(parsed.frontmatter_cursor)
            if desc:
                return desc
        for block in parsed.blocks:
            desc = _description_from_markdown_text(block.content)
            if desc:
                return desc
    docs_path = repo_root / "docs" / "agent-guides" / "rules" / f"{slug}.md"
    if docs_path.is_file():
        desc = _description_from_markdown_text(docs_path.read_text(encoding="utf-8"))
        if desc:
            return desc
    return _truncate_description(f"{slug_to_name(slug)} guidance rule.")


def _description_from_skill_body(path: Path) -> str:
    return _description_from_markdown_text(path.read_text(encoding="utf-8"))


def _resolve_fields(
    slug: str,
    entity: dict,
    missing: list[str],
    *,
    repo_root: Path,
) -> dict[str, object]:
    updates: dict[str, object] = {}
    attrs = dict(entity.get("attributes") or {})
    mdc_path = _find_rule_mdc(slug, repo_root)
    fm: dict[str, object] = {}
    if mdc_path is not None:
        fm = parse_frontmatter(mdc_path.read_text(encoding="utf-8"))

    skill_path = _workspace_skill_path(slug, repo_root)
    description = str(entity.get("description") or "").strip()
    trigger_short = str(attrs.get("trigger_short") or fm.get("trigger_short") or "")
    skill_category = str(attrs.get("skill_category") or fm.get("skill_category") or "")

    if "description" in missing:
        candidate = str(fm.get("description") or "").strip()
        if not candidate and skill_path is not None:
            candidate = _description_from_skill_body(skill_path)
        if not candidate:
            candidate = _description_from_agent_surface(slug, repo_root)
        if candidate:
            updates["description"] = _truncate_description(candidate)

    if "trigger_match_terms" in missing:
        fm_terms = fm.get("trigger_match_terms")
        if isinstance(fm_terms, list) and fm_terms:
            terms = canonicalize_trigger_match_terms([str(v) for v in fm_terms])
        else:
            desc_for_terms = str(updates.get("description") or description or fm.get("description") or "")
            terms = canonicalize_trigger_match_terms(
                derive_trigger_match_terms(
                    slug,
                    trigger_short=trigger_short,
                    skill_category=skill_category,
                    description=desc_for_terms,
                )
            )
        if terms:
            attrs["trigger_match_terms"] = terms
            updates["attributes"] = attrs

    if "paired_rule_pointer" in missing and paired_rule_exists(slug, repo_root):
        attrs = dict(updates.get("attributes") or attrs)
        attrs["paired_rule_pointer"] = f"../.cursor/rules/{slug}.mdc"
        updates["attributes"] = attrs

    return updates


def _patch_entity(
    client: object,
    entity_id: str,
    updates: dict[str, object],
    *,
    dry_run: bool,
) -> bool:
    if not updates:
        return True
    if dry_run:
        print(f"  WOULD PATCH  {entity_id}  {sorted(updates)}")
        return True
    q = urllib.parse.quote(entity_id, safe=":")
    status, body = _request(client, "PATCH", f"/entities/{q}", body=updates)
    if status not in (200, 201):
        print(f"  FAIL  {entity_id}  [PATCH {status}] {body}", file=sys.stderr)
        return False
    print(f"  ok    {entity_id}  [PATCH] {sorted(updates)}")
    return True


def run(*, repo_root: Path, dry_run: bool = False) -> int:
    client = make_sync_client(DEFAULT_CORTEX_URL)
    entities = fetch_discoverable_entities(client)
    pending: list[tuple[str, str, list[str], dict[str, object]]] = []
    for slug, entity in sorted(entities.items()):
        if entity.get("lifecycle") in _SUPPRESSED:
            continue
        fields = extract_renderer_fields(entity, slug)
        missing = _missing_stub_critical(slug, fields, repo_root=repo_root)
        if not missing:
            continue
        entity_id = str(entity.get("id") or f"agent_skill:{slug}")
        updates = _resolve_fields(slug, entity, missing, repo_root=repo_root)
        unresolved = [
            key
            for key in missing
            if key not in updates
            and not (
                key == "trigger_match_terms"
                and isinstance((updates.get("attributes") or {}).get("trigger_match_terms"), list)
            )
            and not (key == "description" and updates.get("description"))
            and not (
                key == "paired_rule_pointer"
                and isinstance((updates.get("attributes") or {}).get("paired_rule_pointer"), str)
            )
        ]
        if unresolved:
            print(
                f"  SKIP  {entity_id}  unresolved {unresolved} (had {missing})",
                file=sys.stderr,
            )
            continue
        pending.append((slug, entity_id, missing, updates))

    print(
        f"Backfill stub-critical fields: {len(pending)} entity/entities "
        f"({'dry-run' if dry_run else 'apply'})"
    )
    failures = 0
    for slug, entity_id, missing, updates in pending:
        print(f"  plan  {slug:40s}  fill {missing}")
        if not _patch_entity(client, entity_id, updates, dry_run=dry_run):
            failures += 1

    if dry_run:
        return 1 if failures else 0

    verdict_status, lines, _ = stub_critical_field_verdict(client, repo_root)
    print(
        f"Post-backfill stub-critical verdict: {verdict_status} "
        f"({len(lines)} problem(s))"
    )
    for line in lines:
        print(f"  - {line}")
    return 1 if failures or verdict_status != "clean" else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", type=Path, default=_REPO)
    args = parser.parse_args(argv)
    return run(repo_root=args.root.resolve(), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
