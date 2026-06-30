#!/usr/bin/env python3
"""Attribute classified scope vocabulary terms to owning skills via properties.source JOIN.

Index-time/batch only — no query-path LLM. Idempotent full-replace of skill_vocabulary.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from cortex_store.routes._skill_suggest_candidates import slug_from_source_uri

from services.rag.property_index import PropertyIndex
from services.rag.vocabulary._skill_attribution import build_skill_vocabulary_rows

logger = logging.getLogger(__name__)

_KEY_PREFIXES = ("prop.name@@", "prop.topic@@")
_DEFAULT_SCOPE = "agent_skills"


def _default_source_prefix() -> str:
    root = os.environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files")
    return f"{root.rstrip('/')}/agent-skills"


async def attribute_skill_vocabulary(
    *,
    scope: str = _DEFAULT_SCOPE,
    source_prefixes: list[str] | None = None,
    db_path: Path | None = None,
) -> int:
    prefixes = source_prefixes or [_default_source_prefix()]
    idx = PropertyIndex(db_path=db_path) if db_path else PropertyIndex()
    await idx.start()
    try:
        scope_rows = idx.load_scope_vocabulary_for_scope(scope)
        if not scope_rows:
            logger.warning("No scope_vocabulary rows for scope=%s — skipping", scope)
            await idx.replace_skill_vocabulary([])
            return 0

        source_counts: list[tuple[str, str, int, int]] = []
        for key_prefix in _KEY_PREFIXES:
            source_counts.extend(
                idx.get_term_counts_by_source(key_prefix, prefixes)
            )

        hint_scores = idx.load_corpus_hint_scores(scope)
        rows = build_skill_vocabulary_rows(
            scope_vocabulary=scope_rows,
            source_term_counts=source_counts,
            corpus_hint_scores=hint_scores,
            slug_from_source=slug_from_source_uri,
        )
        await idx.replace_skill_vocabulary(rows)
        slug_count = len({row[0] for row in rows})
        logger.info(
            "skill_vocabulary: scope=%s rows=%d skills=%d",
            scope,
            len(rows),
            slug_count,
        )
        print(
            f"Attributed {len(rows)} term(s) across {slug_count} skill(s)"
            f" for scope={scope!r}"
        )
        return 0
    finally:
        await idx.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        default=_DEFAULT_SCOPE,
        help=f"RAG scope name (default: {_DEFAULT_SCOPE})",
    )
    parser.add_argument(
        "--source-prefix",
        action="append",
        dest="source_prefixes",
        help="Source path prefix (repeatable; default: $CORTEX_FILES_ROOT/agent-skills)",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Override metadata DB path (default: ~/.rag/store/rag_metadata.db)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return asyncio.run(
        attribute_skill_vocabulary(
            scope=args.scope,
            source_prefixes=args.source_prefixes,
            db_path=args.db_path,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
