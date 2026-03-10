"""Article registry: map source filenames to citation metadata for chunk enrichment.

Registry YAML is read at RAG startup; lookup by filename at index time.
Not watched for changes — load once.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = ["ArticleEntry", "load_registry", "get_entry", "lookup_article"]


@dataclass(slots=True)
class ArticleEntry:
    title: str = ""
    authors: str = ""
    venue: str = ""
    published_date: str = ""
    doi: str = ""
    content_hash: str = ""
    subdirectory: str = ""


def load_registry(path: Path) -> dict[str, ArticleEntry]:
    """Parse registry YAML; return dict keyed by filename (not full path)."""
    if not path.exists():
        return {}
    raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    articles = raw.get("articles")
    if not isinstance(articles, dict):
        return {}
    result: dict[str, ArticleEntry] = {}
    for key, val in articles.items():
        if not isinstance(key, str) or not isinstance(val, dict):
            continue
        v = {k: val[k] for k in val if isinstance(k, str)}
        result[key] = ArticleEntry(
            title=_str(v.get("title")),
            authors=_str(v.get("authors")),
            venue=_str(v.get("venue")),
            published_date=_str(v.get("published_date")),
            doi=_str(v.get("doi")),
            content_hash=_str(v.get("content_hash")),
            subdirectory=_str(v.get("subdirectory")),
        )
    return result


def _str(x: Any) -> str:
    return str(x) if x is not None else ""


def get_entry(registry: dict[str, ArticleEntry], file_path: str) -> ArticleEntry | None:
    """Return registry entry by filename, or None."""
    return registry.get(Path(file_path).name)


def lookup_article(
    registry: dict[str, ArticleEntry], file_path: str
) -> dict[str, str | int | float | bool] | None:
    """Return flat metadata dict for chunk merge, or None if not in registry."""
    entry = get_entry(registry, file_path)
    if entry is None:
        return None
    return {
        "article_title": entry.title,
        "article_authors": entry.authors,
        "article_venue": entry.venue,
        "published_date": entry.published_date,
        "article_doi": entry.doi,
    }
