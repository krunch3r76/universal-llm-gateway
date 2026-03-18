#!/usr/bin/env python3
"""Backfill article metadata from article_registry.yaml via Stargate's article upsert API.

Reads docs/research/article_registry.yaml, maps subdirectories to scopes, and
calls POST /api/v1/rag/article for each entry. Safe to run repeatedly — the
endpoint uses merge semantics (non-empty fields overwrite, empty strings preserve).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

REGISTRY_PATH = Path.home() / ".rag" / "article_registry.yaml"
RESEARCH_DIR = Path(__file__).resolve().parent.parent / "docs" / "research"
STARGATE_URL = "http://localhost:9999/api/v1/rag/article"

SUBDIRECTORY_TO_SCOPE: dict[str, str] = {
    "rag-systems": "rag_systems",
    "prompting": "small_llm_prompting",
    "workflows": "workflows",
    "belief-consistency": "belief_consistency",
    "graph-modeling": "graph_modeling",
    "temporal-provenance": "temporal_provenance",
    "code-retrieval": "code_retrieval",
    "knowledge-management": "knowledge_systems",
    "llm/prompting": "llm_prompting",
}


def load_registry(path: Path) -> dict[str, dict[str, Any]]:
    with open(path) as f:
        data = yaml.safe_load(f)
    articles: dict[str, dict[str, Any]] = data.get("articles", {})
    if not articles:
        print(f"No articles found in {path}", file=sys.stderr)
        sys.exit(1)
    return articles


def build_payload(filename: str, entry: dict[str, Any]) -> dict[str, Any]:
    subdirectory = entry.get("subdirectory", "")
    scope = SUBDIRECTORY_TO_SCOPE.get(subdirectory, "default")
    source_path = str(RESEARCH_DIR / subdirectory / filename)

    return {
        "source_path": source_path,
        "filename": filename,
        "title": entry.get("title", ""),
        "authors": entry.get("authors", ""),
        "venue": entry.get("venue", ""),
        "published_date": str(entry.get("published_date", "")),
        "content_hash": entry.get("content_hash", ""),
        "subdirectory": subdirectory,
        "scope": scope,
    }


def main() -> None:
    articles = load_registry(REGISTRY_PATH)
    print(f"Loaded {len(articles)} entries from {REGISTRY_PATH.name}")

    created = 0
    updated = 0
    errors = 0

    with httpx.Client(timeout=15.0) as client:
        for filename, entry in articles.items():
            payload = build_payload(filename, entry)
            try:
                resp = client.post(STARGATE_URL, json=payload)
                resp.raise_for_status()
                result = resp.json()
                if result.get("created"):
                    created += 1
                else:
                    updated += 1
            except httpx.HTTPStatusError as exc:
                errors += 1
                print(
                    f"  ERROR {filename}: HTTP {exc.response.status_code} - {exc.response.text}",
                    file=sys.stderr,
                )
            except httpx.RequestError as exc:
                errors += 1
                print(f"  ERROR {filename}: request failed - {exc}", file=sys.stderr)
            except Exception as exc:
                errors += 1
                print(f"  ERROR {filename}: unexpected error - {exc}", file=sys.stderr)

    total = created + updated + errors
    print(f"\nDone. Processed {total}/{len(articles)}:")
    print(f"  Created : {created}")
    print(f"  Updated : {updated}")
    print(f"  Errors  : {errors}")

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
