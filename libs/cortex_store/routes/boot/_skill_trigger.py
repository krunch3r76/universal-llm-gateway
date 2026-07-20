"""Resolve boot-card skill trigger text from on-disk SKILL.md (single source)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from cortex_store.guidance_entity import entity_slug_from_id

from ...dispatch_ops._shared import _FILES_ROOT

logger = get_logger("cortex-api.boot._skill_trigger")

_WORKSPACES_ROOT = Path(
    os.environ.get("WORKSPACES_ROOT", "/mnt/torus/projects")
).resolve()

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_TRIGGER_LINE_RE = re.compile(r"^\*\*Trigger:\*\*\s*(.+)$", re.MULTILINE)


def first_sentence(text: str | None) -> str:
    """First sentence of `text` for manifest trigger display."""
    if not text:
        return ""
    return text.split(". ", 1)[0].rstrip(".").strip()


def _parse_frontmatter_description(text: str) -> str | None:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    block = match.group(1)
    for line in block.splitlines():
        if line.startswith("description:"):
            raw = line.split(":", 1)[1].strip()
            if raw.startswith(">-") or raw.startswith("|"):
                return None
            if (raw.startswith('"') and raw.endswith('"')) or (
                raw.startswith("'") and raw.endswith("'")
            ):
                raw = raw[1:-1]
            return raw.strip() or None
    return None


def _parse_trigger_line(text: str) -> str | None:
    match = _TRIGGER_LINE_RE.search(text)
    if not match:
        return None
    return match.group(1).strip() or None


def _resolve_skill_file(source_uri: str | None, slug: str) -> Path | None:
    if source_uri:
        if source_uri.startswith("workspaces://"):
            rest = source_uri.removeprefix("workspaces://")
            candidate = (_WORKSPACES_ROOT / rest).resolve()
            if candidate.is_file():
                return candidate
        elif "://" not in source_uri:
            candidate = (
                Path(source_uri)
                if Path(source_uri).is_absolute()
                else _FILES_ROOT / source_uri
            )
            if candidate.is_file():
                return candidate
            if not Path(source_uri).is_absolute() and source_uri.startswith(
                "docs/agent-guides/"
            ):
                ws_candidate = (
                    _WORKSPACES_ROOT / "universal-llm-gateway" / source_uri
                ).resolve()
                if ws_candidate.is_file():
                    return ws_candidate
        else:
            raw = source_uri.removeprefix("cortex://")
            candidate = (
                Path(source_uri)
                if Path(source_uri).is_absolute()
                else _FILES_ROOT / raw
            )
            if candidate.is_file():
                return candidate
    fallback = _FILES_ROOT / "agent-skills" / f"{slug}.md"
    if fallback.is_file():
        return fallback
    try:
        from claude_bundles.catalog import load_skill_catalog

        repo_root = _WORKSPACES_ROOT / "universal-llm-gateway"
        catalog = load_skill_catalog(repo_root=repo_root)
        plugin_path, _ = catalog.resolve_sot(slug, repo_root)
        if plugin_path.is_file():
            return plugin_path
    except (FileNotFoundError, KeyError, ValueError):
        pass
    return None


def canonical_skill_summary(
    trigger_short: str | None, fallback: str, *, max_chars: int | None = None
) -> str:
    """Canonical skill-summary order: curated trigger_short, else fallback.

    Truncation (ellipsis) applies ONLY to the fallback branch — a curated
    trigger_short is emitted verbatim. Mirrors the legacy boot-card behavior so
    converging the surfaces does not change rendered bytes.
    """
    ts = (trigger_short or "").strip()
    if ts:
        return ts
    fb = (fallback or "").strip()
    if max_chars is not None and len(fb) > max_chars:
        return fb[:max_chars] + "\u2026"
    return fb


def skill_trigger_text(row: dict[str, Any]) -> str:
    """Project manifest trigger from file frontmatter / Trigger line, else entity description."""
    entity_id = str(row.get("id") or "")
    slug = str(row.get("name") or "").strip() or entity_slug_from_id(entity_id)
    path = _resolve_skill_file(row.get("source_uri"), slug)
    if path is not None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("skill trigger read failed for %s: %s", path, exc)
            text = ""
        else:
            for candidate in (
                _parse_frontmatter_description(text),
                _parse_trigger_line(text),
            ):
                if candidate:
                    return first_sentence(candidate)
    return first_sentence(row.get("description"))


def skill_description_text(row: dict[str, Any]) -> str:
    """Boot-aligned summary for skill_suggest — trigger_short, then L1 first sentence."""
    return canonical_skill_summary(row.get("trigger_short"), skill_trigger_text(row))
