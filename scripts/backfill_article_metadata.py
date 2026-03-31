#!/usr/bin/env python3
"""Backfill article metadata from article_registry.yaml via Stargate's article upsert API.

Reads docs/research/article_registry.yaml, maps subdirectories to scopes, and
calls POST /api/v1/rag/article for each entry. Safe to run repeatedly — the
endpoint uses merge semantics (non-empty fields overwrite, empty strings preserve).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, TypedDict

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
    "documentation": "code_documentation",
    "software-agents": "software_agents",
    "event-salience": "event_salience",
    "trading/strategies": "trading_strategies",
    "trading/microstructure": "trading_microstructure",
    "trading/risk": "trading_risk",
    "trading/options": "trading_options",
    "trading/ml-trading": "trading_ml",
    "trading/crypto": "trading_crypto",
    "trading/order-flow": "trading_order_flow",
    "trading/stat-arb": "trading_stat_arb",
    "code-transformation": "code_transformation",
    "trading/execution": "trading_execution",
    "trading/intraday": "trading_intraday",
    "trading/prediction-markets": "trading_prediction_markets",
}


class ArticleEntry(TypedDict):
    subdirectory: str
    title: str
    authors: str
    venue: str
    published_date: str
    content_hash: str


def load_registry(path: Path) -> dict[str, ArticleEntry]:
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
    if subdirectory:
        source_path = str(RESEARCH_DIR / subdirectory / filename)
    else:
        # Decide on appropriate behavior: error, default to RESEARCH_DIR / filename, etc.
        # For now, assuming it should be RESEARCH_DIR / filename if no subdirectory is specified
        source_path = str(RESEARCH_DIR / filename)
        # Consider adding logging here: logger.warning(f"Article {filename} has no subdirectory specified.")

    payload_fields = ["title", "authors", "venue", "content_hash", "subdirectory"]
    payload = {
        "source_path": source_path,
        "filename": filename,
        "scope": scope,
        **{field: entry.get(field, "") for field in payload_fields},
    }
    payload["published_date"] = str(entry.get("published_date", ""))
    return payload


def main() -> None:
    articles = load_registry(REGISTRY_PATH)
    print(f"Loaded {len(articles)} entries from {REGISTRY_PATH.name}")
    # logger.info(f"Starting article metadata backfill for {len(articles)} articles.")

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
            # Consider removing this general Exception catch if all expected errors are handled
            # or if unhandled exceptions should cause the script to fail loudly.
            # If kept, add more specific logging or error reporting.
            except Exception as exc:
                errors += 1
                print(
                    f"  ERROR {filename}: unexpected error - {type(exc).__name__} - {exc}",
                    file=sys.stderr,
                )

    total = created + updated + errors
    print(f"\nDone. Processed {total}/{len(articles)}:")
    print(f"  Created : {created}")
    print(f"  Updated : {updated}")
    print(f"  Errors  : {errors}")

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
