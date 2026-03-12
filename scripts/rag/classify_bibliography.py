#!/usr/bin/env python3
"""Backfill chunk classification metadata in ChromaDB using LLM calls.

Usage:
    python scripts/rag/classify_bibliography.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import chromadb
import httpx

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen3-5-9b-q8-0-262144"
DEFAULT_ENDPOINT = "http://localhost:9999/v1/chat/completions"
DEFAULT_CHROMA_PATH = "/home/io/.rag/store"
DEFAULT_COLLECTION = "knowledge"
DEFAULT_UPDATE_BATCH_SIZE = 50
DEFAULT_REQUEST_BATCH_SIZE = 512
DEFAULT_CONCURRENCY = 128
DEFAULT_MAX_RETRIES = 4

SYSTEM_PROMPT_BIBLIOGRAPHY = """You are classifying RAG corpus chunks.
Return JSON only: {"is_junk": true/false}.

Set is_junk=true when the chunk is mostly bibliography, boilerplate, or low-value retrieval noise, including:
- pure references/bibliography lists
- author bios, affiliations, acknowledgments, funding notes
- copyright/license boilerplate
- table-of-contents fragments
- dense cross-reference text without substantive content
- abstract-only fragments detached from body context
- URL/link dump sections

Set is_junk=false when the chunk contains substantive technical content, including:
- literature review paragraphs with meaningful compare/contrast
- methodology and implementation details (even with heavy citations)
- original analysis, definitions, or technical claims."""

SYSTEM_PROMPT_NON_INTELLIGIBLE = """You are classifying RAG corpus chunks.
Return JSON only: {"is_non_intelligible": true/false}.

Set is_non_intelligible=true when the chunk is unusable text for semantic retrieval, including:
- OCR garbage, random symbols, unreadable corruption
- extremely fragmented text with no coherent sentence meaning
- malformed extraction output where words are mostly broken/noise
- pages containing almost no language content beyond artifacts

Set is_non_intelligible=false when the chunk is coherent enough to convey meaningful technical content,
even if terse, partially formatted, or includes citations/table snippets."""


@dataclass(frozen=True, slots=True)
class ClassificationProfile:
    """Target-specific classification configuration."""

    metadata_key: str
    response_key: str
    system_prompt: str
    task_line: str


PROFILES: dict[str, ClassificationProfile] = {
    "bibliography": ClassificationProfile(
        metadata_key="is_bibliography",
        response_key="is_junk",
        system_prompt=SYSTEM_PROMPT_BIBLIOGRAPHY,
        task_line="Classify this chunk for retrieval usefulness.",
    ),
    "non_intelligible": ClassificationProfile(
        metadata_key="is_non_intelligible",
        response_key="is_non_intelligible",
        system_prompt=SYSTEM_PROMPT_NON_INTELLIGIBLE,
        task_line="Classify whether this chunk is non-intelligible retrieval noise.",
    ),
}


@dataclass(slots=True)
class ChunkRecord:
    """Chunk payload needed for classification and metadata update."""

    chunk_id: str
    document: str
    metadata: dict[str, Any]


def _build_user_prompt(*, text: str, response_key: str, task_line: str) -> str:
    return (
        f"{task_line}\n"
        f"Return JSON with key {response_key} (boolean).\n\n"
        "<chunk>\n"
        f"{text}\n"
        "</chunk>"
    )


async def classify_chunk(
    *,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    text: str,
    model: str,
    endpoint: str,
    max_retries: int,
    profile: ClassificationProfile,
) -> bool:
    """Return True if chunk is bibliography/junk."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": profile.system_prompt},
            {
                "role": "user",
                "content": _build_user_prompt(
                    text=text,
                    response_key=profile.response_key,
                    task_line=profile.task_line,
                ),
            },
        ],
        "temperature": 0.1,
        "max_tokens": 32,
        "response_format": {"type": "json_object"},
    }
    for attempt in range(max_retries + 1):
        try:
            async with semaphore:
                response = await client.post(endpoint, json=payload)
                response.raise_for_status()
                body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            value = parsed.get(profile.response_key)
            if not isinstance(value, bool):
                raise ValueError(
                    f"Expected boolean {profile.response_key}; got: {parsed!r}"
                )
            return value
        except (
            httpx.TimeoutException,
            httpx.HTTPError,
            json.JSONDecodeError,
            ValueError,
        ):
            if attempt >= max_retries:
                raise
            backoff_seconds = (0.5 * (2**attempt)) + random.uniform(0, 0.25)
            await asyncio.sleep(backoff_seconds)
    raise RuntimeError("unreachable")


def iter_batches[T](items: list[T], batch_size: int) -> Iterable[list[T]]:
    """Yield fixed-size slices from a list."""
    for index in range(0, len(items), batch_size):
        yield items[index : index + batch_size]


def load_chunks(
    *,
    chroma_path: str,
    collection_name: str,
) -> tuple[chromadb.Collection, list[ChunkRecord]]:
    """Load all chunk IDs/documents/metadata from ChromaDB collection."""
    client = chromadb.PersistentClient(path=chroma_path)
    collection = client.get_collection(collection_name)
    result = collection.get(include=["documents", "metadatas"])
    ids = result.get("ids") or []
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    records: list[ChunkRecord] = []
    for chunk_id, document, metadata in zip(ids, documents, metadatas, strict=True):
        records.append(
            ChunkRecord(
                chunk_id=str(chunk_id),
                document=document or "",
                metadata=dict(metadata or {}),
            )
        )
    return collection, records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill chunk metadata classification using LLM."
    )
    parser.add_argument(
        "--target",
        choices=sorted(PROFILES),
        default="bibliography",
        help="Classification target profile.",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help="Classification model ID."
    )
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help="Stargate chat completions endpoint.",
    )
    parser.add_argument(
        "--chroma-path",
        default=DEFAULT_CHROMA_PATH,
        help="Path to ChromaDB PersistentClient store.",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help="ChromaDB collection name.",
    )
    parser.add_argument(
        "--update-batch-size",
        type=int,
        default=DEFAULT_UPDATE_BATCH_SIZE,
        help="Chroma metadata update batch size.",
    )
    parser.add_argument(
        "--request-batch-size",
        type=int,
        default=DEFAULT_REQUEST_BATCH_SIZE,
        help="Number of chunks to classify per async request batch.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="Max in-flight Stargate classification requests.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="HTTP timeout for each Stargate request.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Retry attempts per chunk on transient request/parse failures.",
    )
    parser.add_argument(
        "--force-all",
        action="store_true",
        help="Reclassify all chunks even if is_bibliography already exists.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional limit for dry evaluation (0 = all chunks).",
    )
    return parser.parse_args()


async def classify_batch(
    *,
    records: list[ChunkRecord],
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    model: str,
    endpoint: str,
    max_retries: int,
    profile: ClassificationProfile,
) -> list[bool | None]:
    """Classify one batch concurrently and return is_junk flags in order."""
    tasks = [
        asyncio.create_task(
            classify_chunk(
                client=client,
                semaphore=semaphore,
                text=record.document,
                model=model,
                endpoint=endpoint,
                max_retries=max_retries,
                profile=profile,
            )
        )
        for record in records
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: list[bool | None] = []
    for record, result in zip(records, results, strict=True):
        if isinstance(result, Exception):
            logger.error(
                "Classification failed for chunk_id=%s: %s", record.chunk_id, result
            )
            out.append(None)
            continue
        out.append(result)
    return out


async def run(args: argparse.Namespace) -> None:
    profile = PROFILES[args.target]
    collection, records = load_chunks(
        chroma_path=args.chroma_path,
        collection_name=args.collection,
    )
    if not args.force_all:
        records = [
            record for record in records if profile.metadata_key not in record.metadata
        ]
    if args.limit > 0:
        records = records[: args.limit]

    total = len(records)
    if total == 0:
        print("No chunks found; nothing to classify.")
        return

    print(f"Loaded {total} chunks from '{args.collection}' at {args.chroma_path}")
    print(f"Target: {args.target} -> metadata_key={profile.metadata_key}")
    print(f"Model: {args.model}")
    print(f"Endpoint: {args.endpoint}")
    print(
        "Async classification: "
        f"concurrency={args.concurrency}, request_batch_size={args.request_batch_size}, "
        f"update_batch_size={args.update_batch_size}, max_retries={args.max_retries}"
    )

    junk_count = 0
    clean_count = 0
    classified = 0
    updated = 0
    failed = 0
    pending_ids: list[str] = []
    pending_metadatas: list[dict[str, Any]] = []
    semaphore = asyncio.Semaphore(args.concurrency)
    timeout = httpx.Timeout(args.timeout_seconds)
    limits = httpx.Limits(
        max_keepalive_connections=args.concurrency,
        max_connections=max(args.concurrency * 2, args.concurrency),
    )

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        for batch in iter_batches(records, args.request_batch_size):
            flags = await classify_batch(
                records=batch,
                client=client,
                semaphore=semaphore,
                model=args.model,
                endpoint=args.endpoint,
                max_retries=args.max_retries,
                profile=profile,
            )
            for record, is_junk in zip(batch, flags, strict=True):
                classified += 1
                if is_junk is None:
                    failed += 1
                    continue
                record.metadata[profile.metadata_key] = is_junk
                pending_ids.append(record.chunk_id)
                pending_metadatas.append(record.metadata)
                if is_junk:
                    junk_count += 1
                else:
                    clean_count += 1

                if len(pending_ids) >= args.update_batch_size:
                    collection.update(ids=pending_ids, metadatas=pending_metadatas)
                    updated += len(pending_ids)
                    pending_ids.clear()
                    pending_metadatas.clear()

            print(
                f"[{classified}/{total}] updated={updated} junk={junk_count} clean={clean_count} failed={failed}"
            )

    if pending_ids:
        collection.update(ids=pending_ids, metadatas=pending_metadatas)
        updated += len(pending_ids)
        print(
            f"[{total}/{total}] updated={updated} junk={junk_count} clean={clean_count}"
        )

    print("\nClassification complete")
    print(f"Total classified: {total}")
    print(f"Junk count: {junk_count}")
    print(f"Clean count: {clean_count}")
    print(f"Failed count: {failed}")


def _stamp_watermark() -> None:
    """Record bibliography classification completion in the property index watermarks."""
    from services.rag.property_index import PropertyIndex

    async def _stamp() -> None:
        idx = PropertyIndex()
        await idx.start()
        try:
            await idx.stamp_watermark("bibliography")
        finally:
            await idx.stop()

    asyncio.run(_stamp())
    print("Watermark 'bibliography' stamped.")


def main() -> None:
    args = parse_args()
    if args.update_batch_size <= 0:
        raise ValueError("--update-batch-size must be > 0")
    if args.request_batch_size <= 0:
        raise ValueError("--request-batch-size must be > 0")
    if args.concurrency <= 0:
        raise ValueError("--concurrency must be > 0")
    if args.max_retries < 0:
        raise ValueError("--max-retries must be >= 0")
    if args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds must be > 0")
    asyncio.run(run(args))
    _stamp_watermark()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    main()
