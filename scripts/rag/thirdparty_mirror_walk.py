"""Walk and validate third-party mirror trees under ``docs/thirdparty/{provider}/``."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CANONICAL_TIERS = ("upstream", "summaries", "product")
META_BASENAMES = frozenset({"README.md", "refresh.md", "mirror-policy.md"})

_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass(slots=True)
class FileRecord:
    path: Path
    rel: str
    tier: str
    title: str
    source_url: str
    refreshed: str
    derived_from: list[str] = field(default_factory=list)
    content_hash: str = ""


@dataclass(slots=True)
class WalkReport:
    files: list[FileRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter, body) — frontmatter is empty when absent."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}, text
    body = text[end + 5 :]
    if not isinstance(loaded, dict):
        return {}, body
    return loaded, body


def _extract_h1(body: str) -> str:
    match = _H1_RE.search(body)
    return match.group(1).strip() if match else ""


def _classify_tier(rel_parts: tuple[str, ...]) -> str:
    """Return canonical tier from path parts; `unclassified` when flat."""
    if not rel_parts:
        return "unclassified"
    first = rel_parts[0]
    if first in CANONICAL_TIERS:
        return first
    return "unclassified"


def _build_record(*, file_path: Path, provider_root: Path) -> FileRecord:
    text = file_path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    rel_parts = file_path.relative_to(provider_root).parts
    tier = _classify_tier(rel_parts)
    title = (
        str(fm.get("thirdparty_title") or "").strip()
        or _extract_h1(body)
        or file_path.stem.replace("-", " ").title()
    )
    refreshed = str(fm.get("thirdparty_refreshed") or "").strip()
    source_url = str(fm.get("thirdparty_source_url") or "").strip()
    derived_raw = fm.get("thirdparty_derived_from") or []
    derived_from = [str(item).strip() for item in derived_raw if str(item).strip()]
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return FileRecord(
        path=file_path,
        rel="/".join(rel_parts),
        tier=tier,
        title=title,
        source_url=source_url,
        refreshed=refreshed,
        derived_from=derived_from,
        content_hash=digest,
    )


def walk_provider(provider_root: Path) -> WalkReport:
    report = WalkReport()
    if not provider_root.exists() or not provider_root.is_dir():
        report.warnings.append(f"Provider directory not found: {provider_root}")
        return report
    canonical_present = any((provider_root / t).is_dir() for t in CANONICAL_TIERS)
    for path in sorted(provider_root.rglob("*.md")):
        if path.name in META_BASENAMES:
            continue
        record = _build_record(file_path=path, provider_root=provider_root)
        report.files.append(record)
        if record.tier == "unclassified" and canonical_present:
            report.warnings.append(f"File outside canonical tiers: {record.rel}")
        if record.tier == "upstream" and not record.source_url:
            report.warnings.append(
                f"upstream/ file missing thirdparty_source_url: {record.rel}"
            )
        if record.tier == "summaries" and not record.derived_from:
            report.warnings.append(
                f"summaries/ file missing thirdparty_derived_from: {record.rel}"
            )
        if not record.refreshed:
            report.warnings.append(f"File missing thirdparty_refreshed: {record.rel}")
    if not canonical_present:
        report.warnings.append(
            f"Provider has flat layout (no upstream/summaries/product): {provider_root.name}"
        )
    return report
