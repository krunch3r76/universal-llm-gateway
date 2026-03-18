#!/usr/bin/env python3
"""Populate the articles table from article_registry.yaml via the RAG API.

Reads the curated YAML registry, validates that each entry has a title and
content_hash, and upserts each entry through the RAG service's POST /article
endpoint (via Stargate passthrough or direct RAG URL).

Usage:
    python scripts/populate-articles.py                    # via Stargate (default)
    python scripts/populate-articles.py --rag-url unix:///tmp/universal-protocol/rag.sock
    python scripts/populate-articles.py --dry-run
"""

from __future__ import annotations

import argparse
import contextlib
import sys
import time
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "libs"))

import httpx  # noqa: E402

from services.rag.article_registry import ArticleEntry, load_registry  # noqa: E402

REGISTRY_PATH = Path.home() / ".rag" / "article_registry.yaml"
RESEARCH_DIR = WORKSPACE / "docs" / "research"

_DEFAULT_STARGATE_URL = "http://localhost:9999"
_STARGATE_ARTICLE_PATH = "/api/v1/rag/article"
_RAG_ARTICLE_PATH = "/article"


def validate_entries(
    registry: dict[str, ArticleEntry],
) -> tuple[list[tuple[str, ArticleEntry]], list[str]]:
    """Split registry into valid entries (with title + content_hash) and skipped filenames."""
    valid: list[tuple[str, ArticleEntry]] = []
    skipped: list[str] = []
    for filename, entry in registry.items():
        if not entry.title or not entry.content_hash:
            skipped.append(filename)
            continue
        valid.append((filename, entry))
    return valid, skipped


def _build_body(filename: str, entry: ArticleEntry) -> dict[str, str]:
    """Build the JSON request body for a single article upsert."""
    subdir = entry.subdirectory or ""
    if subdir:
        source_path = str((RESEARCH_DIR / subdir / filename).resolve())
    else:
        source_path = str((RESEARCH_DIR / filename).resolve())
    return {
        "source_path": source_path,
        "filename": filename,
        "title": entry.title,
        "authors": entry.authors,
        "venue": entry.venue,
        "published_date": entry.published_date,
        "doi": entry.doi,
        "abstract": entry.abstract,
        "content_hash": entry.content_hash,
        "subdirectory": entry.subdirectory,
        "scope": "all",
    }


def _make_client(rag_url: str | None, stargate_url: str) -> tuple[httpx.Client, str]:
    """Create an httpx client and return (client, article_endpoint_path).

    When rag_url is set, connect directly to RAG (supports unix:// UDS).
    Otherwise, connect to Stargate and use the passthrough path.
    """
    if rag_url is not None:
        from transport_utils.rag_client import parse_rag_url

        uds_path, base_url = parse_rag_url(rag_url)
        if uds_path:
            transport = httpx.HTTPTransport(uds=uds_path)
            return httpx.Client(
                transport=transport, base_url=base_url, timeout=15.0
            ), _RAG_ARTICLE_PATH
        return httpx.Client(base_url=base_url, timeout=15.0), _RAG_ARTICLE_PATH

    return httpx.Client(base_url=stargate_url, timeout=15.0), _STARGATE_ARTICLE_PATH


@contextlib.contextmanager
def _client_context(rag_url: str | None, stargate_url: str):
    """Context manager that yields (client, path) and closes the client on exit."""
    client, path = _make_client(rag_url, stargate_url)
    try:
        yield (client, path)
    finally:
        client.close()


def populate(
    *,
    dry_run: bool = False,
    rag_url: str | None = None,
    stargate_url: str = _DEFAULT_STARGATE_URL,
) -> None:
    """Load registry and upsert articles to RAG. Raises on missing or empty registry."""
    if not REGISTRY_PATH.exists():
        raise FileNotFoundError(f"Registry not found: {REGISTRY_PATH}")

    registry = load_registry(REGISTRY_PATH)
    if not registry:
        raise ValueError("Registry is empty or failed to parse.")

    valid, skipped = validate_entries(registry)
    print(
        f"Registry: {len(registry)} total, {len(valid)} valid, {len(skipped)} skipped"
    )
    if skipped:
        for name in skipped:
            print(f"  SKIP (missing title or content_hash): {name}")

    if dry_run:
        print("\n[dry-run] Would upsert the following articles:")
        for filename, entry in valid:
            print(f"  {filename}: {entry.title[:60]}...")
        return

    created = 0
    updated = 0
    errors = 0
    t0 = time.monotonic()

    with _client_context(rag_url, stargate_url) as (client, path):
        for filename, entry in valid:
            body = _build_body(filename, entry)
            try:
                resp = client.post(path, json=body)
                resp.raise_for_status()
                result = resp.json()
                if result.get("created"):
                    created += 1
                else:
                    updated += 1
            except httpx.HTTPStatusError as exc:
                errors += 1
                print(
                    f"  ERROR {filename}: {exc.response.status_code} {exc.response.text}"
                )
            except httpx.RequestError as exc:
                errors += 1
                print(f"  ERROR {filename}: {exc}")

    elapsed = time.monotonic() - t0
    target = "Stargate" if rag_url is None else rag_url
    print(f"\nDone via {target} in {elapsed:.1f}s:")
    print(f"  Created: {created}")
    print(f"  Updated: {updated}")
    if errors:
        print(f"  Errors:  {errors}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Populate articles table from YAML registry via RAG API"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without writing",
    )
    parser.add_argument(
        "--rag-url",
        default=None,
        help="Direct RAG URL (e.g. unix:///tmp/universal-protocol/rag.sock). "
        "If omitted, uses Stargate passthrough.",
    )
    parser.add_argument(
        "--stargate-url",
        default=_DEFAULT_STARGATE_URL,
        help=f"Stargate URL (default: {_DEFAULT_STARGATE_URL})",
    )
    args = parser.parse_args()
    try:
        populate(
            dry_run=args.dry_run,
            rag_url=args.rag_url,
            stargate_url=args.stargate_url,
        )
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)
    except ValueError as e:
        print(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
