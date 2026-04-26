"""Startup / reconcile / watcher automatic scope-freshness repair hooks."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from services.rag.config import RagConfig
from services.rag.corpus_hints import (
    detect_stale_scopes,
    scopes_touching_watch_path,
)
from services.rag.vocabulary import configured_scopes_map, run_scope_freshness_repair

from . import state
from .lifecycle_constants import POST_INDEX_STEPS, STARTUP_SCOPE_REPAIR_RETRY_DELAYS_S

logger = logging.getLogger(__name__)


def _maybe_clear_post_index_stale_after_repair(config: RagConfig) -> None:
    """If strict gate was set at startup, clear it once watermarks catch up."""
    if not state._post_index_stale:
        return
    still = state._property_index.check_watermarks(
        list(POST_INDEX_STEPS), reference="reindex"
    )
    if not still:
        state._post_index_stale = False
        logger.info("Post-index strict gate cleared after automatic scope repair")


def _post_index_still_stale() -> bool:
    pi = state._property_index
    if pi is None:
        return False
    return bool(pi.check_watermarks(list(POST_INDEX_STEPS), reference="reindex"))


async def _run_startup_scope_freshness_repair(config: RagConfig) -> None:
    pi = state._property_index
    if pi is None:
        return
    for attempt, delay_s in enumerate(
        (*STARTUP_SCOPE_REPAIR_RETRY_DELAYS_S, None),
        start=1,
    ):
        stale_steps = set(
            pi.check_watermarks(list(POST_INDEX_STEPS), reference="reindex")
        )
        stale = detect_stale_scopes(
            property_index=pi,
            configured_scopes=configured_scopes_map(config),
            scope_filter=None,
        )
        if not stale and stale_steps & {"corpus_hints", "vocabulary"}:
            stale = sorted(config.scopes)
        if not stale:
            _maybe_clear_post_index_stale_after_repair(config)
            return
        logger.warning(
            "Automatic scope freshness repair (startup attempt %d): %d stale scopes: %s",
            attempt,
            len(stale),
            stale,
        )
        await run_scope_freshness_repair(
            property_index=pi,
            config=config,
            stale_scopes=stale,
            event_bus=state._event_bus,
            trigger="startup",
        )
        _maybe_clear_post_index_stale_after_repair(config)
        if not _post_index_still_stale() or delay_s is None:
            return
        logger.warning(
            "Startup scope freshness repair still stale; retrying in %.0fs",
            delay_s,
        )
        await asyncio.sleep(delay_s)


async def _post_reconcile_scope_freshness(prefix_paths: list[str]) -> None:
    cfg = state._config
    pi = state._property_index
    if cfg is None or pi is None or not prefix_paths:
        return
    affected: set[str] = set()
    for p in prefix_paths:
        affected |= scopes_touching_watch_path(cfg, Path(p))
    scope_filter = affected if affected else None
    stale = detect_stale_scopes(
        property_index=pi,
        configured_scopes=configured_scopes_map(cfg),
        scope_filter=scope_filter,
    )
    if not stale:
        return
    logger.warning(
        "Automatic scope freshness repair (reconcile): %d stale scopes: %s",
        len(stale),
        stale,
    )
    await run_scope_freshness_repair(
        property_index=pi,
        config=cfg,
        stale_scopes=stale,
        event_bus=state._event_bus,
        trigger="reconcile",
    )
    _maybe_clear_post_index_stale_after_repair(cfg)


async def _watcher_debounced_scope_freshness(scopes: set[str]) -> None:
    cfg = state._config
    pi = state._property_index
    if cfg is None or pi is None or not scopes:
        return
    stale = detect_stale_scopes(
        property_index=pi,
        configured_scopes=configured_scopes_map(cfg),
        scope_filter=scopes,
    )
    if not stale:
        return
    logger.warning(
        "Automatic scope freshness repair (watcher): %d stale scopes: %s",
        len(stale),
        stale,
    )
    await run_scope_freshness_repair(
        property_index=pi,
        config=cfg,
        stale_scopes=stale,
        event_bus=state._event_bus,
        trigger="watcher",
    )
    _maybe_clear_post_index_stale_after_repair(cfg)
