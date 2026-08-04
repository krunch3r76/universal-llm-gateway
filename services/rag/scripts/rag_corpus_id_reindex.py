#!/usr/bin/env python3
"""One-time corpus sweep to migrate chunk IDs to content-addressed scheme (S3)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

IndexOneFn = Callable[..., Awaitable[object]]
ListSourcesFn = Callable[[], list[str]]


@dataclass(slots=True)
class CorpusReindexResult:
    """Aggregate counters from a corpus ID reindex sweep."""

    total: int = 0
    scheduled: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped_missing: int = 0
    dry_run: bool = False
    failures: list[tuple[str, str]] = field(default_factory=list)


async def run_corpus_id_reindex(
    *,
    list_sources: ListSourcesFn,
    index_one: IndexOneFn,
    prefix: str | None = None,
    batch_size: int = 8,
    rate_limit_s: float = 0.0,
    dry_run: bool = False,
) -> CorpusReindexResult:
    """Walk indexed sources and admit each with force reindex (B6).

    Uses ``force=True`` and ``operation=\"reindex\"`` so ``source_identity`` and
    legacy ID schemes are rewritten under the new path-key composition.
    """
    sources = list_sources()
    if prefix:
        sources = [s for s in sources if s.startswith(prefix)]
    result = CorpusReindexResult(total=len(sources), dry_run=dry_run)
    if dry_run:
        result.scheduled = len(sources)
        return result

    for batch_start in range(0, len(sources), max(1, batch_size)):
        batch = sources[batch_start : batch_start + batch_size]
        tasks = []
        batch_sources: list[str] = []
        for source in batch:
            path = Path(source)
            if not path.exists():
                result.skipped_missing += 1
                logger.warning("Corpus reindex skip missing file: %s", source)
                continue
            result.scheduled += 1
            batch_sources.append(source)
            tasks.append(
                index_one(
                    path,
                    None,
                    force=True,
                    emit_skip_event=False,
                    operation="reindex",
                )
            )
        if tasks:
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            for source, outcome in zip(batch_sources, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    result.failed += 1
                    result.failures.append((source, repr(outcome)))
                    logger.warning("Corpus reindex failed for %s: %s", source, outcome)
                else:
                    result.succeeded += 1
        if rate_limit_s > 0 and batch_start + batch_size < len(sources):
            await asyncio.sleep(rate_limit_s)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate RAG corpus chunk IDs to content-addressed scheme."
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="Optional source path prefix filter",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="Concurrent reindex batch size (default 8)",
    )
    parser.add_argument(
        "--rate-limit-s",
        type=float,
        default=0.0,
        help="Sleep between batches in seconds (default 0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List sources that would be reindexed without executing",
    )
    return parser


async def _main_async(args: argparse.Namespace) -> CorpusReindexResult:
    """Wire live RAG service dependencies for CLI execution."""
    from services.rag.property_index import PropertyIndex
    from services.rag.rag_service.indexing import _index_file

    # CLI expects PROPERTY_INDEX_DB or service boot — operator sets env before run.
    db_path = args.property_index_db
    prop_index = PropertyIndex(db_path)

    async def _index(path: Path, *_a: object, **kwargs: object) -> object:
        return await _index_file(path, **kwargs)

    return await run_corpus_id_reindex(
        list_sources=prop_index.get_indexed_sources,
        index_one=_index,
        prefix=args.prefix,
        batch_size=args.batch_size,
        rate_limit_s=args.rate_limit_s,
        dry_run=args.dry_run,
    )


def main() -> None:
    """CLI entry: corpus ID reindex sweep."""
    parser = _build_parser()
    parser.add_argument(
        "--property-index-db",
        required=True,
        help="Path to property_index SQLite database",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    started = time.monotonic()
    result = asyncio.run(_main_async(args))
    elapsed = time.monotonic() - started
    logger.info(
        "Corpus reindex complete: total=%d scheduled=%d ok=%d failed=%d "
        "missing=%d dry_run=%s elapsed=%.1fs",
        result.total,
        result.scheduled,
        result.succeeded,
        result.failed,
        result.skipped_missing,
        result.dry_run,
        elapsed,
    )
    if result.failures:
        for source, err in result.failures[:20]:
            logger.error("  failed: %s → %s", source, err)


if __name__ == "__main__":
    main()
