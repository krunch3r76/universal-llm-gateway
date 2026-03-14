#!/usr/bin/env python3
"""Classify corpus hint terms into vocabulary registers per scope.

Reads per-scope IDF terms from the metadata database, sends each scope's terms
through an LLM classification prompt, and writes register-structured output to
the ``scope_vocabulary`` table in the metadata database.

Usage:
    python scripts/rag/classify_vocabulary.py [--model MODEL_ID] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import requests

from services.rag.config import load_config
from services.rag.corpus_hints import load_corpus_hints

logger = logging.getLogger(__name__)

STARGATE_URL = "http://localhost:9999/v1/chat/completions"
DEFAULT_MODEL = "rag-context"  # Will be overridden; we use a direct model
CLASSIFICATION_PROMPT = """\
You are classifying vocabulary terms for a RAG retrieval system.
Given a scope name, its description, and a list of IDF-scored terms extracted
from that scope's corpus, classify each term into one of these registers:

- **practitioner**: tool names, framework names, implementation patterns,
  product names, file formats, CLI commands, workflow terminology
  (e.g. Obsidian, Zettelkasten, Neo4j, Cypher, vault, backlinks)
- **academic**: formal concepts, theoretical frameworks, research terminology,
  algorithmic names, mathematical constructs
  (e.g. personal knowledge graph, entity-centric, ontology, reification)
- **specification**: standard names, protocol names, specification documents,
  formal language names, W3C/ISO/IEEE identifiers
  (e.g. RDF, OWL, SHACL, PROV-O, JSON-LD, SPARQL, SQL/PGQ)

Rules:
1. A term may appear in only one register (choose the best fit).
2. Drop terms that are too generic, ambiguous, or clearly noise.
3. You may add 2-4 additional high-value terms per register that are
   obviously missing but central to the scope. Mark these with a trailing
   asterisk (*) so the caller knows they were inferred.
4. Return valid JSON only.

Output format:
{
  "practitioner": ["term1", "term2", ...],
  "academic": ["term1", "term2", ...],
  "specification": ["term1", "term2", ...]
}
"""


def classify_scope(
    scope: str,
    description: str,
    terms: list[str],
    model: str,
) -> dict[str, list[str]] | None:
    """Classify scope terms into practitioner/academic/specification registers.

    Returns parsed register buckets on success, otherwise ``None`` when the
    model call or JSON parsing fails.
    """
    user_msg = (
        f"Scope: {scope}\n"
        f"Description: {description}\n"
        f"Terms to classify:\n{json.dumps(terms)}\n\n"
        "Return JSON with keys: practitioner, academic, specification."
    )
    try:
        resp = requests.post(
            STARGATE_URL,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": CLASSIFICATION_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.2,
                "max_tokens": 1024,
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        logger.error("Classification failed for scope '%s': %s", scope, e)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify corpus hints into vocabulary registers"
    )
    parser.add_argument(
        "--model",
        default="qwen3-14b-q4-k-m-32768",
        help="Model ID for classification",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan without calling LLM",
    )
    args = parser.parse_args()

    config = load_config()
    hints_map = load_corpus_hints()

    if not hints_map:
        print(
            "No corpus hints found. Run `python -m services.rag.corpus_hints` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    scope_descriptions: dict[str, str] = {}
    for scope_name, scope_def in config.scopes.items():
        scope_descriptions[scope_name] = getattr(scope_def, "description", "") or ""

    print(f"Loaded {len(hints_map)} scopes from rag_metadata.db corpus_hints table")
    for scope, text in sorted(hints_map.items()):
        terms = [t.strip() for t in text.split(",") if t.strip()]
        desc = scope_descriptions.get(scope, "")
        print(f"  {scope}: {len(terms)} terms — {desc[:60]}")

    if args.dry_run:
        print("\n--dry-run: would classify the above scopes. Exiting.")
        return

    result: dict[str, dict[str, list[str]]] = {}
    for scope, text in sorted(hints_map.items()):
        terms = [t.strip() for t in text.split(",") if t.strip()]
        if not terms:
            continue
        desc = scope_descriptions.get(scope, "")
        print(f"\nClassifying {scope} ({len(terms)} terms)...")
        classified = classify_scope(scope, desc, terms, args.model)
        if classified:
            clean: dict[str, list[str]] = {}
            for reg in ("practitioner", "academic", "specification"):
                reg_terms = classified.get(reg, [])
                if isinstance(reg_terms, list):
                    clean[reg] = [
                        str(t) for t in reg_terms if isinstance(t, str) and t.strip()
                    ]
            result[scope] = clean
            for reg, ts in clean.items():
                print(f"  {reg}: {len(ts)} terms")
        else:
            print("  FAILED — skipping scope")

    if not result:
        print("\nNo scopes classified successfully.", file=sys.stderr)
        sys.exit(1)

    _write_scope_vocabulary_db(result)
    print(f"\nWritten DB rows for {len(result)} scopes to ~/.rag/store/rag_metadata.db")

    _stamp_watermark()


def _write_scope_vocabulary_db(vocabulary: dict[str, dict[str, list[str]]]) -> None:
    """Persist register-structured vocabulary to the metadata SQLite database."""
    import asyncio

    from services.rag.property_index import PropertyIndex

    async def _write() -> None:
        idx = PropertyIndex()
        await idx.start()
        try:
            await idx.replace_scope_vocabulary(vocabulary)
        finally:
            await idx.stop()

    try:
        asyncio.run(_write())
    except Exception as exc:
        logger.error("Failed to write scope vocabulary to SQLite: %s", exc)
        raise


def _stamp_watermark() -> None:
    """Record vocabulary classification completion in the property index watermarks."""
    import asyncio

    from services.rag.property_index import PropertyIndex

    async def _stamp() -> None:
        idx = PropertyIndex()
        await idx.start()
        try:
            await idx.stamp_watermark("vocabulary")
        finally:
            await idx.stop()

    try:
        asyncio.run(_stamp())
    except Exception as exc:
        logger.error("Failed to stamp 'vocabulary' watermark: %s", exc)
        raise
    print("Watermark 'vocabulary' stamped.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    main()
