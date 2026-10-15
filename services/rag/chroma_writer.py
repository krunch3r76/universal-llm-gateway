"""Single-thread Chroma writer executor for the RAG serving process (friction:39359).

Routes blocking ``collection.*`` calls off the asyncio loop through one
``ThreadPoolExecutor`` worker. Caps are from leg 2 at **default** embedding dim
4096 (``qwen3-embedding-8b-q8-0-4096`` in ``config/_models.py``); ``rag.yaml``
override was not read for the ladder (N6).

On-loop CPU-bound callers **not** routed here (N4 deferrals, see plan inventory):
``list_known_sources`` inside prefix find, ``get_pending_files`` at startup,
``check_watermarks`` in the watcher — sqlite/property-index work, not Chroma Rust.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import chromadb

logger = logging.getLogger(__name__)

# Default dim 4096 ladder (leg 2 friction:39359); rag.yaml not read for caps.
UPSERT_BATCH_CAP = 500
DELETE_IDS_CAP = 2000
GET_PAGE_CAP = 4000
UPDATE_IDS_CAP = 500

_executor: ThreadPoolExecutor | None = None
# SIGTERM during a multi-slice job was SIGKILLed at ~9.8 s. The join stays on
# the loop thread but must leave time for the rest of graceful shutdown.
_CHROMA_SHUTDOWN_WAIT_S = 3.0

def init_chroma_executor() -> None:
    """Create the module-level single-worker executor (``lifecycle._startup``)."""
    global _executor
    if _executor is not None:
        return
    real_thread = threading.Thread

    def _daemon_thread(*args: Any, **kwargs: Any) -> threading.Thread:
        kwargs["daemon"] = True
        return real_thread(*args, **kwargs)

    threading.Thread = _daemon_thread  # type: ignore[misc]
    try:
        _executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rag-chroma-writer"
        )
        _executor.submit(lambda: None).result()
    finally:
        threading.Thread = real_thread  # type: ignore[misc]


def shutdown_chroma_executor() -> None:
    """Join the writer for at most ``_CHROMA_SHUTDOWN_WAIT_S`` (``lifecycle._shutdown``)."""
    global _executor
    if _executor is None:
        return
    executor = _executor
    _executor = None
    finished = threading.Event()

    def _stop() -> None:
        try:
            executor.shutdown(wait=True, cancel_futures=False)
        finally:
            finished.set()

    threading.Thread(target=_stop, name="rag-chroma-shutdown", daemon=True).start()
    if not finished.wait(_CHROMA_SHUTDOWN_WAIT_S):
        logger.warning(
            "chroma_executor_shutdown_exceeded_s=%.1f", _CHROMA_SHUTDOWN_WAIT_S
        )


def chroma_executor() -> ThreadPoolExecutor | None:
    """Return the live executor, or None before startup / after shutdown."""
    return _executor


async def run_chroma[T](fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a synchronous Chroma callable on the single writer thread."""
    if _executor is None:
        raise RuntimeError("Chroma writer executor is not initialized")
    fn_name = getattr(fn, "__name__", repr(fn))

    def _invoke() -> T:
        return fn(*args, **kwargs)

    submitted = _executor.submit(_invoke)
    start = time.perf_counter()
    try:
        # Poll instead of wrap_future: cancelling the awaiter must not
        # cancel a queued Chroma job (N-a).
        while not submitted.done():
            await asyncio.sleep(0.005)
        return submitted.result()
    finally:
        chroma_call_ms = (time.perf_counter() - start) * 1000.0
        logger.info("chroma_call_ms=%.3f fn=%s", chroma_call_ms, fn_name)


def _log_collection_call(op: str, started: float, **fields: object) -> None:
    """One ``collection.*`` call, not the whole executor job (N5 / N-d)."""
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    extra = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info("chroma_call_ms=%.3f fn=collection.%s %s", elapsed_ms, op, extra)


def chroma_delete_ids(collection: chromadb.Collection, ids: list[str]) -> None:
    """Delete ids in slices of at most ``DELETE_IDS_CAP``."""
    if not ids:
        return
    for start in range(0, len(ids), DELETE_IDS_CAP):
        batch = ids[start : start + DELETE_IDS_CAP]
        started = time.perf_counter()
        collection.delete(ids=batch)
        _log_collection_call("delete", started, n=len(batch))


def chroma_update_metadatas(
    collection: chromadb.Collection,
    ids: list[str],
    metadatas: list[dict[str, Any]],
) -> None:
    """Update metadatas in slices of at most ``UPDATE_IDS_CAP``."""
    if not ids:
        return
    for start in range(0, len(ids), UPDATE_IDS_CAP):
        end = start + UPDATE_IDS_CAP
        batch_ids = ids[start:end]
        started = time.perf_counter()
        collection.update(ids=batch_ids, metadatas=metadatas[start:end])
        _log_collection_call("update", started, n=len(batch_ids))


def chroma_get_by_source_ids(
    collection: chromadb.Collection, source: str
) -> list[str]:
    """Return all chunk ids for a source, paged at ``GET_PAGE_CAP``."""
    existing = chroma_get_where(
        collection, where={"source": source}, include=[]
    )
    return list(existing.get("ids") or [])


def chroma_get_where(
    collection: chromadb.Collection,
    *,
    where: dict[str, object] | None = None,
    include: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    """``collection.get`` with optional paging at ``GET_PAGE_CAP``."""
    page_include = ["metadatas"] if include is None else include
    if limit is not None:
        started = time.perf_counter()
        page = collection.get(
            where=where, include=page_include, limit=limit, offset=offset
        )
        _log_collection_call("get", started, n=len(page.get("ids") or []))
        return page
    page_limit = GET_PAGE_CAP
    merged_ids: list[str] = []
    merged_docs: list[Any] = []
    merged_metas: list[Any] = []
    page_offset = offset
    while True:
        started = time.perf_counter()
        page = collection.get(
            where=where,
            include=page_include,
            limit=page_limit,
            offset=page_offset,
        )
        _log_collection_call("get", started, n=len(page.get("ids") or []))
        ids = page.get("ids") or []
        if not ids:
            break
        merged_ids.extend(ids)
        if include and "documents" in include:
            merged_docs.extend(page.get("documents") or [])
        if include is None or "metadatas" in include:
            merged_metas.extend(page.get("metadatas") or [])
        if len(ids) < page_limit:
            break
        page_offset += page_limit
    out: dict[str, Any] = {"ids": merged_ids}
    if include and "documents" in include:
        out["documents"] = merged_docs
    if include is None or "metadatas" in include:
        out["metadatas"] = merged_metas
    return out
