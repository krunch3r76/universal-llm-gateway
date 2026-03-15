"""Article registry helpers for YAML migration and DB-backed runtime lookups.

Runtime uses the SQLite ``articles`` table as the source of truth. YAML parsing
is retained only for one-time migration during startup when the DB table is
empty and a legacy registry file exists.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

__all__ = [
    "ArticleEntry",
    "load_registry",
    "load_registry_from_db",
    "replace_article_rows",
    "get_entry",
    "lookup_article",
    "to_article_rows",
]


@dataclass(slots=True)
class ArticleEntry:
    title: str = ""
    authors: str = ""
    venue: str = ""
    published_date: str = ""
    doi: str = ""
    abstract: str = ""
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
            abstract=_str(v.get("abstract")),
            content_hash=_str(v.get("content_hash")),
            subdirectory=_str(v.get("subdirectory")),
        )
    return result


def load_registry_from_db(db_path: Path) -> dict[str, ArticleEntry]:
    """Load article metadata from SQLite keyed by filename for runtime lookups."""
    if not db_path.exists():
        return {}
    try:
        with contextlib.closing(
            sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        ) as conn:
            rows = conn.execute(
                "SELECT filename, title, authors, venue, published_date, doi, "
                "abstract, content_hash, subdirectory "
                "FROM articles ORDER BY filename ASC"
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("Failed to load article registry from DB %s: %s", db_path, exc)
        return {}
    result: dict[str, ArticleEntry] = {}
    for row in rows:
        filename = _str(row[0]).strip()
        if not filename:
            continue
        result[filename] = ArticleEntry(
            title=_str(row[1]),
            authors=_str(row[2]),
            venue=_str(row[3]),
            published_date=_str(row[4]),
            doi=_str(row[5]),
            abstract=_str(row[6]),
            content_hash=_str(row[7]),
            subdirectory=_str(row[8]),
        )
    return result


def replace_article_rows(
    db_path: Path,
    rows: list[tuple[str, str, str, str, str, str, str, str, str, str, str]],
) -> None:
    """Replace all rows in the SQLite ``articles`` table in one transaction."""
    if not db_path.exists():
        return
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM articles")
            if rows:
                conn.executemany(
                    "INSERT INTO articles ("
                    "source_path, filename, title, authors, venue, published_date, "
                    "doi, abstract, scope, content_hash, subdirectory"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


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


def to_article_rows(
    registry: dict[str, ArticleEntry],
    *,
    source_root: Path,
    scope_resolver: Callable[[str], str],
) -> list[tuple[str, str, str, str, str, str, str, str, str, str, str]]:
    """Convert filename-keyed article metadata into normalized SQLite article row tuples."""
    rows: list[tuple[str, str, str, str, str, str, str, str, str, str, str]] = []
    for filename, entry in registry.items():
        source_path = str((source_root / filename).resolve())
        scope = scope_resolver(source_path)
        rows.append(
            (
                source_path,
                filename,
                entry.title,
                entry.authors,
                entry.venue,
                entry.published_date,
                entry.doi,
                entry.abstract,
                scope,
                entry.content_hash,
                entry.subdirectory,
            )
        )
    return rows
