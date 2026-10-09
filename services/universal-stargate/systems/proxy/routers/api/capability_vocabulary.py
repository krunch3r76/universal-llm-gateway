"""Capability vocabulary loaded from config/capabilities.yaml."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from transport_utils import EVENTS_QUERY_SOCK, JOBS_SOCK

_UPSTREAMS = {
    "events_query": lambda: f"unix://{EVENTS_QUERY_SOCK}",
    "jobs": lambda: f"unix://{JOBS_SOCK}",
    "web_fetcher": lambda: os.environ.get("WEB_FETCHER_URL", "").strip(),
}
_LINK_URI = re.compile(r"<([^>]+)>")


@dataclass(frozen=True)
class Category:
    """One capability category."""

    name: str
    description: str
    implementation: str
    upstream_symbol: str
    origin_prefix: str
    auth_env: str = ""

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
    """Return the capabilities YAML path, honoring CAPABILITIES_YAML when set."""
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
    """Drop the cached vocabulary so the next load re-reads the file."""
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
                auth_env=str(entry.get("auth_env") or ""),
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


def map_origin(value: str, origin_prefix: str, category: str) -> str | None:
    """Rewrite one origin path onto the capability prefix, or return None.

    A value equal to the origin prefix, or starting with that prefix plus a
    slash, becomes ``/api/v1/capabilities/{category}`` plus the same suffix.
    Anything else is left for the caller to reject when it is a Location.
    """
    if value == origin_prefix or value.startswith(origin_prefix + "/"):
        return f"/api/v1/capabilities/{category}{value[len(origin_prefix):]}"
    return None


def bearer_for(auth_env: str) -> str | None:
    """Return the configured bearer token, or None when the env name is unset.

    An empty auth_env means the category does not swap Authorization. A set
    name with an empty value is treated as unconfigured so the relay can 503
    instead of forwarding the caller's credential.
    """
    if not auth_env:
        return ""
    token = os.environ.get(auth_env, "")
    return token or None


def rewrite_link_header(value: str, origin_prefix: str, category: str) -> str:
    """Rewrite angle-bracket URIs in a Link header that sit under the origin."""

    def _one(match: re.Match[str]) -> str:
        mapped = map_origin(match.group(1), origin_prefix, category)
        return f"<{mapped}>" if mapped else match.group(0)

    return _LINK_URI.sub(_one, value)


def rewrite_json_value(node: Any, origin_prefix: str, category: str) -> Any:
    """Rewrite href strings and OpenAPI path keys that still name the origin."""
    if isinstance(node, dict):
        rewritten: dict[Any, Any] = {}
        for key, value in node.items():
            new_key = key
            if key == "paths" and isinstance(value, dict):
                value = {
                    map_origin(str(path), origin_prefix, category) or str(path): child
                    for path, child in value.items()
                }
            if isinstance(key, str) and key == "href" and isinstance(value, str):
                mapped = map_origin(value, origin_prefix, category)
                rewritten[new_key] = mapped if mapped else rewrite_json_value(
                    value, origin_prefix, category
                )
                continue
            rewritten[new_key] = rewrite_json_value(value, origin_prefix, category)
        return rewritten
    if isinstance(node, list):
        return [rewrite_json_value(item, origin_prefix, category) for item in node]
    return node
