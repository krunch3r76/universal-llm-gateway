#!/usr/bin/env python3
"""Backfill chunk noise metadata in ChromaDB using LLM calls.

Usage:
    python scripts/rag/classify_noise.py
    python scripts/rag/classify_noise.py --target describe
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

import chromadb
import httpx

from services.rag.chunk_filters import (
    chunk_metadata_is_noise,
    normalize_noise_metadata,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen3-5-9b-q8-0-262144"
DEFAULT_ENDPOINT = "http://localhost:9999/v1/chat/completions"
DEFAULT_CHROMA_PATH = "/home/io/.rag/store"
DEFAULT_COLLECTION = "knowledge"
DEFAULT_UPDATE_BATCH_SIZE = 50
DEFAULT_REQUEST_BATCH_SIZE = 512
DEFAULT_CONCURRENCY = 128
DEFAULT_MAX_RETRIES = 4

SYSTEM_PROMPT_NOISE = """You are classifying RAG corpus chunks.
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

SYSTEM_PROMPT_DESCRIBE = """You summarize RAG corpus chunks for operator review (noise / low-value retrieval candidates).
Return JSON only: {"noise_description": "<string>"}.

Write 1-2 sentences describing what the chunk contains. Be specific (e.g. 'Dense HTML table of VIX futures settlement prices').
Bibliography-style content (reference lists, author-year blocks, low prose density) is a common noise class — describe it plainly when applicable; it is lower retrieval priority than substantive technical prose.
If the chunk is clearly substantive technical prose that should NOT be treated as noise, say that explicitly (helps catch false positives)."""


@dataclass(frozen=True, slots=True)
class ClassificationProfile:
    """Target-specific classification configuration."""

    metadata_key: str
    response_key: str
    system_prompt: str
    task_line: str
    response_kind: Literal["bool", "text"] = "bool"
    max_output_tokens: int = 32


PROFILES: dict[str, ClassificationProfile] = {
    "bibliography": ClassificationProfile(
        metadata_key="is_noise",
        response_key="is_junk",
        system_prompt=SYSTEM_PROMPT_NOISE,
        task_line="Classify this chunk for retrieval usefulness.",
        response_kind="bool",
        max_output_tokens=32,
    ),
    "non_intelligible": ClassificationProfile(
        metadata_key="is_non_intelligible",
        response_key="is_non_intelligible",
        system_prompt=SYSTEM_PROMPT_NON_INTELLIGIBLE,
        task_line="Classify whether this chunk is non-intelligible retrieval noise.",
        response_kind="bool",
        max_output_tokens=32,
    ),
    "describe": ClassificationProfile(
        metadata_key="noise_description",
        response_key="noise_description",
        system_prompt=SYSTEM_PROMPT_DESCRIBE,
        task_line="Summarize this chunk for operator review.",
        response_kind="text",
        max_output_tokens=256,
    ),
}


@dataclass(slots=True)
class ChunkRecord:
    """Chunk payload needed for classification and metadata update."""

    chunk_id: str
    document: str
    metadata: dict[str, Any]


def _build_user_prompt(
    *,
    text: str,
    response_key: str,
    task_line: str,
    response_kind: Literal["bool", "text"],
) -> str:
    type_hint = "boolean" if response_kind == "bool" else "string, 1-3 sentences"
    return (
        f"{task_line}\n"
        f"Return JSON with key {response_key} ({type_hint}).\n\n"
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
    max_doc_chars: int = 0,
) -> bool | str | None:
    """Return classification value, or None on persistent failure."""
    if max_doc_chars > 0 and len(text) > max_doc_chars:
        text = text[:max_doc_chars]
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
                    response_kind=profile.response_kind,
                ),
            },
        ],
        "temperature": 0.1,
        "max_tokens": profile.max_output_tokens,
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
            if profile.response_kind == "bool":
                if not isinstance(value, bool):
                    raise ValueError(
                        f"Expected boolean {profile.response_key}; got: {parsed!r}"
                    )
                return value
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Expected non-empty string {profile.response_key}; got: {parsed!r}"
                )
            return value.strip()[:2000]
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
        description="Backfill chunk noise / classification metadata using LLM."
    )
    parser.add_argument(
        "--target",
        choices=sorted(PROFILES),
        default="bibliography",
        help="Classification profile: bibliography -> is_noise; describe -> noise_description.",
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
        "--max-doc-chars",
        type=int,
        default=8000,
        help=(
            "Truncate document text to this many characters before classification "
            "(0 = no limit). Default 8000 guards against context-limit 400s when "
            "the model has parallel slots that reduce effective context per slot."
        ),
    )
    parser.add_argument(
        "--force-all",
        action="store_true",
        help="Reclassify all chunks even if target metadata key already exists.",
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
    max_doc_chars: int = 0,
) -> list[bool | str | None]:
    """Classify one batch concurrently; order matches *records*."""
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
                max_doc_chars=max_doc_chars,
            )
        )
        for record in records
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: list[bool | str | None] = []
    for record, result in zip(records, results, strict=True):
        if isinstance(result, Exception):
            logger.error(
                "Classification failed for chunk_id=%s: %s", record.chunk_id, result
            )
            out.append(None)
            continue
        out.append(result)
    return out


async def run(args: argparse.Namespace) -> bool:
    profile = PROFILES[args.target]
    collection, records = load_chunks(
        chroma_path=args.chroma_path,
        collection_name=args.collection,
    )
    if profile.response_kind == "text":
        records = [r for r in records if chunk_metadata_is_noise(r.metadata)]
        if not args.force_all:
            records = [
                r
                for r in records
                if not (
                    isinstance(r.metadata.get("noise_description"), str)
                    and str(r.metadata.get("noise_description", "")).strip()
                )
            ]
    elif not args.force_all:
        records = [
            record for record in records if profile.metadata_key not in record.metadata
        ]
    if args.limit > 0:
        records = records[: args.limit]

    total = len(records)
    if total == 0:
        print("No chunks found; nothing to classify.")
        return False

    print(f"Loaded {total} chunks from '{args.collection}' at {args.chroma_path}")
    print(f"Target: {args.target} -> metadata_key={profile.metadata_key}")
    print(f"Model: {args.model}")
    print(f"Endpoint: {args.endpoint}")
    print(
        "Async classification: "
        f"concurrency={args.concurrency}, request_batch_size={args.request_batch_size}, "
        f"update_batch_size={args.update_batch_size}, max_retries={args.max_retries}"
    )

    positive_count = 0
    negative_count = 0
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
                max_doc_chars=args.max_doc_chars,
            )
            for record, value in zip(batch, flags, strict=True):
                classified += 1
                if value is None:
                    failed += 1
                    continue
                if profile.response_kind == "bool":
                    record.metadata[profile.metadata_key] = value
                    if profile.metadata_key == "is_noise":
                        normalize_noise_metadata(record.metadata)
                    if value:
                        positive_count += 1
                    else:
                        negative_count += 1
                else:
                    record.metadata[profile.metadata_key] = value
                    positive_count += 1
                pending_ids.append(record.chunk_id)
                pending_metadatas.append(record.metadata)

                if len(pending_ids) >= args.update_batch_size:
                    collection.update(ids=pending_ids, metadatas=pending_metadatas)
                    updated += len(pending_ids)
                    pending_ids.clear()
                    pending_metadatas.clear()

            print(
                f"[{classified}/{total}] updated={updated} "
                f"positive={positive_count} negative={negative_count} failed={failed}"
            )

    if pending_ids:
        collection.update(ids=pending_ids, metadatas=pending_metadatas)
        updated += len(pending_ids)
        print(
            f"[{total}/{total}] updated={updated} "
            f"positive={positive_count} negative={negative_count}"
        )

    print("\nClassification complete")
    print(f"Total classified: {total}")
    print(f"Positive / described: {positive_count}")
    print(f"Negative (bool only): {negative_count}")
    print(f"Failed count: {failed}")
    return True


def _stamp_watermark() -> None:
    """Record LLM noise classification completion in the property index watermarks."""

    from services.rag.property_index import PropertyIndex

    async def _stamp() -> None:
        idx = PropertyIndex()
        await idx.start()
        try:
            await idx.stamp_watermark("noise")
        finally:
            await idx.stop()

    asyncio.run(_stamp())
    print("Watermark 'noise' stamped.")


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
    did_work = asyncio.run(run(args))
    if did_work:
        _stamp_watermark()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    main()
