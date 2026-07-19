"""CLI entry for one-shot corpus hints generation and Chroma backfill."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from universal_logging import get_logger

from services.rag.corpus_hints.constants import (
    DEFAULT_KEY_PREFIXES,
    DEFAULT_MAX_CHUNKS_NAME,
    DEFAULT_MAX_CHUNKS_TOPIC,
    DEFAULT_MIN_CHUNKS_NAME,
    DEFAULT_MIN_CHUNKS_TOPIC,
    GENERIC_BLOCKLIST,
)
from services.rag.corpus_hints.term_scoring import is_structural_noise
from services.rag.corpus_hints.update import update_corpus_hints
from services.rag.property_index import PropertyIndex

logger = get_logger(__name__)

__all__ = ["main"]


def _build_chunk_source_map() -> dict[str, str]:
    """Build chunk_id→source mapping from Chroma metadata for backfill."""
    import chromadb

    store_path = Path.home() / ".rag" / "store"
    client = chromadb.PersistentClient(path=str(store_path))
    collection = client.get_collection("knowledge")
    all_data = collection.get(include=["metadatas"])
    ids: list[str] = all_data.get("ids") or []
    metas: list[dict[str, object]] = all_data.get("metadatas") or []

    chunk_to_source: dict[str, str] = {}
    for cid, meta in zip(ids, metas, strict=True):
        src = meta.get("source") if isinstance(meta, dict) else None
        if isinstance(src, str) and src:
            chunk_to_source[cid] = src

    print(f"ChromaDB chunks with source metadata: {len(chunk_to_source)}/{len(ids)}")
    return chunk_to_source


def main() -> None:
    """Run one-shot corpus-hints generation from the local property index."""
    args = sys.argv[1:]
    do_backfill = "--backfill" in args

    cli_scope: str | None = None
    if "--scope" in args:
        idx_pos = args.index("--scope")
        if idx_pos + 1 < len(args):
            cli_scope = args[idx_pos + 1]

    no_entity_boost = "--no-entity-boost" in args
    no_blocklist = "--no-blocklist" in args

    from services.rag.config import load_config as _load_config

    exclude_scopes: set[str] = set()

    config = _load_config()
    configured_scopes_map: dict[str, list[str]] = {
        name: sdef.prefixes
        for name, sdef in config.scopes.items()
        if name not in exclude_scopes
    }

    chunk_to_source: dict[str, str] | None = None
    if do_backfill:
        chunk_to_source = _build_chunk_source_map()

    async def _run() -> dict[str, str]:
        idx = PropertyIndex()
        await idx.start()
        try:
            if chunk_to_source is not None:
                updated = await idx.backfill_source(chunk_to_source)
                print(f"Property rows backfilled: {updated}\n")

            total_chunks = idx.get_total_chunks()
            total_docs = idx.get_total_docs()
            print(f"Total distinct chunks: {total_chunks}")
            print(f"Total distinct docs (source files): {total_docs}")

            for prefix in DEFAULT_KEY_PREFIXES:
                all_terms = idx.get_term_counts_by_scope(prefix)
                if prefix == "prop.topic@@":
                    min_c, max_c = DEFAULT_MIN_CHUNKS_TOPIC, DEFAULT_MAX_CHUNKS_TOPIC
                else:
                    min_c, max_c = DEFAULT_MIN_CHUNKS_NAME, DEFAULT_MAX_CHUNKS_NAME
                in_band = 0
                blocked = 0
                noise = 0
                out = 0
                has_doc_count = 0
                for _scope, term, chunk_count, doc_count in all_terms:
                    if chunk_count < min_c or chunk_count > max_c:
                        out += 1
                    elif is_structural_noise(term):
                        noise += 1
                    elif term.lower() in GENERIC_BLOCKLIST:
                        blocked += 1
                    else:
                        in_band += 1
                    if doc_count > 0:
                        has_doc_count += 1
                print(
                    f"  {prefix}: {in_band} candidates, {blocked} blocklisted,"
                    f" {noise} noise, {out} out-of-band,"
                    f" {has_doc_count}/{len(all_terms)} with doc freq"
                )

            excl_msg = (
                f" (excluded: {', '.join(sorted(exclude_scopes))})"
                if exclude_scopes
                else ""
            )
            print(
                f"\nProcessing {len(configured_scopes_map)} configured scopes{excl_msg}"
            )

            tuning_kwargs: dict[str, object] = {
                "configured_scopes": configured_scopes_map,
            }
            if cli_scope is not None:
                tuning_kwargs["scope"] = cli_scope
            if no_entity_boost:
                tuning_kwargs["entity_boost_hyphen"] = 1.0
                tuning_kwargs["entity_boost_single"] = 1.0
            if no_blocklist:
                tuning_kwargs["blocklist_override"] = frozenset()

            result = await update_corpus_hints(idx, **tuning_kwargs)
            if result:
                await idx.stamp_watermark("corpus_hints")
            return result
        finally:
            await idx.stop()

    result = asyncio.run(_run())
    if not result:
        print("No hints generated (empty property index?)", file=sys.stderr)
        sys.exit(1)

    print(f"\nGenerated hints for {len(result)} scope(s):")
    for scope_name, terms in sorted(result.items()):
        term_list = [t.strip() for t in terms.split(",") if t.strip()]
        print(f"  {scope_name}: {len(term_list)} terms")

    print(f"\nWritten DB rows to: {Path.home() / '.rag' / 'store' / 'rag_metadata.db'}")
