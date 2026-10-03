"""Capability vocabulary loaded from config/capabilities.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from transport_utils import EVENTS_QUERY_SOCK

_UPSTREAMS = {
    "events_query": lambda: f"unix://{EVENTS_QUERY_SOCK}",
}


@dataclass(frozen=True)
class Category:
    """One capability category."""

    name: str
    description: str
    implementation: str
    upstream_symbol: str
    origin_prefix: str

    def upstream_url(self) -> str:
        return _UPSTREAMS[self.upstream_symbol]()


@dataclass
class Vocabulary:
    """Loaded categories plus skip records."""

    categories: list[Category]
    skips: list[dict[str, str]]
    mtime: float


_CACHE: Vocabulary | None = None


def vocabulary_path() -> Path:
    override = os.environ.get("CAPABILITIES_YAML")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[6] / "config" / "capabilities.yaml"


def load_vocabulary(*, member_names: set[str] | None = None) -> Vocabulary:
    """Re-read the file when its mtime changes."""
    global _CACHE
    path = vocabulary_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    if (
        _CACHE is not None
        and _CACHE.mtime == mtime
        and member_names is None
    ):
        return _CACHE
    categories, skips = _parse(path, member_names or set())
    loaded = Vocabulary(categories=categories, skips=skips, mtime=mtime)
    if member_names is None:
        _CACHE = loaded
    return loaded


def reset_vocabulary_cache() -> None:
    global _CACHE
    _CACHE = None


def _parse(
    path: Path, member_names: set[str]
) -> tuple[list[Category], list[dict[str, str]]]:
    skips: list[dict[str, str]] = []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        skips.append({"category": "", "reason": f"malformed: {exc}"})
        return [], skips
    block = raw.get("categories") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        skips.append({"category": "", "reason": "malformed: categories missing"})
        return [], skips
    categories: list[Category] = []
    for name, entry in block.items():
        reason = _reject(str(name), entry, member_names)
        if reason:
            skips.append({"category": str(name), "reason": reason})
            continue
        categories.append(
            Category(
                name=str(name),
                description=str(entry.get("description") or ""),
                implementation=str(entry["implementation"]),
                upstream_symbol=str(entry["upstream"]),
                origin_prefix=str(entry["origin_prefix"]),
            )
        )
    return categories, skips


def _reject(name: str, entry: Any, member_names: set[str]) -> str | None:
    if not isinstance(entry, dict):
        return "malformed"
    if name in member_names:
        return "collides with member id"
    for key in ("implementation", "upstream", "origin_prefix"):
        if not entry.get(key):
            return "malformed"
    symbol = str(entry["upstream"])
    if symbol not in _UPSTREAMS:
        return "unknown upstream"
    return None
