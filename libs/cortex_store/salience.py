"""Composite salience scoring engine for Cortex entities (v2.2 §5, §11.2).

Orchestrates batch scoring, EST dual-track gating, and fingerprint-based
cache invalidation. Component scoring functions live in ``scoring.py``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

from .db import query
from .scoring import (
    batch_contextual,
    batch_frequency,
    batch_structural,
    batch_temporal,
    entity_fingerprint,
    fast_state_hash,
)

logger = get_logger("cortex-api.salience")

# Per-persona salience weights: (temporal, structural, contextual, frequency)
PERSONA_WEIGHTS: dict[str, tuple[float, float, float, float]] = {
    "web": (0.30, 0.25, 0.30, 0.15),
    "cursor": (0.40, 0.15, 0.35, 0.10),
    "api": (0.25, 0.30, 0.20, 0.25),
}

SURPRISE_THRESHOLD = 0.15  # γ — EST slow-track gating
_CONTEXTUAL_ACTIVATION_THRESHOLD = 3


@dataclass
class SalienceResult:
    entity_id: str
    entity_name: str
    entity_type: str
    salience_score: float
    temporal_score: float
    structural_score: float
    contextual_score: float
    frequency_score: float
    surprise: float
    boot_treatment: str
    domain: str | None


def _get_weights(
    persona: str, has_contextual: bool
) -> tuple[float, float, float, float]:
    """Return (temporal, structural, contextual, frequency) weights.

    When *has_contextual* is False (cold start or fewer than
    ``_CONTEXTUAL_ACTIVATION_THRESHOLD`` session queries), contextual
    weight is redistributed proportionally among the other three.
    """
    w_t, w_s, w_c, w_f = PERSONA_WEIGHTS.get(persona, PERSONA_WEIGHTS["web"])
    if has_contextual:
        return w_t, w_s, w_c, w_f
    active_sum = w_t + w_s + w_f
    return w_t / active_sum, w_s / active_sum, 0.0, w_f / active_sum


def _has_contextual_signal(
    conn: sqlite3.Connection, agent: str, session_id: str | None
) -> bool:
    """True when the agent has enough access log entries to activate contextual."""
    if session_id:
        rows = query(
            conn,
            "SELECT 1 FROM entity_access_log "
            "WHERE agent = ? AND session_id = ? LIMIT ?",
            (agent, session_id, _CONTEXTUAL_ACTIVATION_THRESHOLD),
        )
    else:
        rows = query(
            conn,
            "SELECT 1 FROM entity_access_log "
            "WHERE agent = ? AND created_at > datetime('now', '-2 hours') LIMIT ?",
            (agent, _CONTEXTUAL_ACTIVATION_THRESHOLD),
        )
    return len(rows) >= _CONTEXTUAL_ACTIVATION_THRESHOLD


def _extract_domain(attrs_raw: str | None) -> str | None:
    if not attrs_raw:
        return None
    try:
        return (
            json.loads(attrs_raw).get("domain") if isinstance(attrs_raw, str) else None
        )
    except (ValueError, TypeError):
        return None


def _build_cache_result(
    eid: str,
    entity_map: dict[str, dict[str, Any]],
    cached: dict[str, Any],
    domain: str | None,
    *,
    ctx_score: float = 0.0,
    weights: tuple[float, float, float, float] | None = None,
) -> SalienceResult:
    """Build result from cache, optionally applying contextual overlay."""
    e = entity_map[eid]
    ts = cached.get("temporal_score", 0.0)
    ss = cached.get("structural_score", 0.0)
    fs = cached.get("frequency_score", 0.0)

    if weights and ctx_score > 0.0:
        w_t, w_s, w_c, w_f = weights
        composite = w_t * ts + w_s * ss + w_c * ctx_score + w_f * fs
    else:
        composite = cached["salience_score"]

    return SalienceResult(
        entity_id=eid,
        entity_name=e["name"],
        entity_type=e["type"],
        salience_score=composite,
        temporal_score=ts,
        structural_score=ss,
        contextual_score=ctx_score,
        frequency_score=fs,
        surprise=cached.get("last_surprise") or 0.0,
        boot_treatment=cached.get("boot_section_cache") or "one_line",
        domain=domain,
    )


_UPSERT_SQL = """
    INSERT INTO entity_salience_cache
      (entity_id, salience_score, temporal_score, structural_score,
       contextual_score, frequency_score, fast_state_hash,
       slow_state_hash, last_surprise, fingerprint, computed_at,
       boot_section_cache)
    VALUES (?, ?, ?, ?, 0.0, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
    ON CONFLICT(entity_id) DO UPDATE SET
      salience_score=excluded.salience_score,
      temporal_score=excluded.temporal_score,
      structural_score=excluded.structural_score,
      contextual_score=excluded.contextual_score,
      frequency_score=excluded.frequency_score,
      fast_state_hash=excluded.fast_state_hash,
      slow_state_hash=excluded.slow_state_hash,
      last_surprise=excluded.last_surprise,
      fingerprint=excluded.fingerprint,
      computed_at=excluded.computed_at,
      boot_section_cache=excluded.boot_section_cache
"""


def compute_all_salience(
    conn: sqlite3.Connection,
    persona: str = "web",
    t_now: datetime | None = None,
    *,
    force: bool = False,
    entity_id_filter: str | None = None,
    agent: str = "web",
    session_id: str | None = None,
) -> tuple[list[SalienceResult], int, int]:
    """Compute salience for all entities (or one). Returns (results, hits, misses).

    Stable components (temporal, structural, frequency) are cached with
    fingerprint-based invalidation.  Contextual is computed fresh per
    request as a dynamic overlay — never stored in the cache.
    """
    if t_now is None:
        t_now = datetime.now(UTC)

    if entity_id_filter:
        entities = query(
            conn,
            "SELECT id, type, name, attributes FROM entities WHERE id = ?",
            (entity_id_filter,),
        )
    else:
        entities = query(
            conn,
            "SELECT id, type, name, attributes FROM entities "
            "WHERE status != 'reaped' AND NOT ("
            "  type = 'todo' AND workflow_state != 'open'"
            ")",
        )

    if not entities:
        return [], 0, 0

    entity_ids = [e["id"] for e in entities]
    entity_map = {e["id"]: e for e in entities}
    entity_domains = {e["id"]: _extract_domain(e["attributes"]) for e in entities}

    existing_cache: dict[str, dict[str, Any]] = {}
    if not force:
        cache_rows = query(conn, "SELECT * FROM entity_salience_cache")
        existing_cache = {r["entity_id"]: dict(r) for r in cache_rows}

    cache_hits, cache_misses = 0, 0
    fingerprints: dict[str, str] = {}
    skip_set: set[str] = set()

    if not force:
        for eid in entity_ids:
            fp = entity_fingerprint(conn, eid)
            fingerprints[eid] = fp
            cached = existing_cache.get(eid)
            if cached and cached.get("fingerprint") == fp:
                cache_hits += 1
                skip_set.add(eid)
            else:
                cache_misses += 1
    else:
        cache_misses = len(entity_ids)

    recompute_ids = [eid for eid in entity_ids if eid not in skip_set]
    recompute_domains = {eid: entity_domains[eid] for eid in recompute_ids}

    temporal_scores = (
        batch_temporal(conn, recompute_domains, t_now) if recompute_ids else {}
    )
    structural_scores = batch_structural(conn, recompute_ids) if recompute_ids else {}
    frequency_scores = batch_frequency(conn, recompute_ids) if recompute_ids else {}

    if structural_scores:
        max_s = max(structural_scores.values()) or 1.0
        structural_scores = {eid: v / max_s for eid, v in structural_scores.items()}

    ctx_scores = batch_contextual(conn, entity_ids, agent, session_id, t_now)
    has_ctx = _has_contextual_signal(conn, agent, session_id)

    w_t, w_s, w_c, w_f = _get_weights(persona, has_ctx)
    # Stable weights always use 3-component redistribution (cache + EST baseline)
    sw_t, sw_s, _, sw_f = _get_weights(persona, False)

    results: list[SalienceResult] = []

    for eid in entity_ids:
        cs = ctx_scores.get(eid, 0.0) if has_ctx else 0.0

        if eid in skip_set:
            results.append(
                _build_cache_result(
                    eid,
                    entity_map,
                    existing_cache[eid],
                    entity_domains.get(eid),
                    ctx_score=cs,
                    weights=(w_t, w_s, w_c, w_f) if has_ctx else None,
                )
            )
            continue

        ts = temporal_scores.get(eid, 0.0)
        ss = structural_scores.get(eid, 0.0)
        fs = frequency_scores.get(eid, 0.0)

        stable_composite = sw_t * ts + sw_s * ss + sw_f * fs
        composite = w_t * ts + w_s * ss + w_c * cs + w_f * fs

        fp = fingerprints.get(eid) or entity_fingerprint(conn, eid)
        fsh = fast_state_hash(ts, ss, fs)

        old = existing_cache.get(eid)
        old_slow_composite = (
            old["salience_score"] if old and old.get("slow_state_hash") else None
        )
        surprise = (
            abs(stable_composite - old_slow_composite)
            if old_slow_composite is not None
            else 1.0
        )
        boot_treatment = "full_section" if surprise > SURPRISE_THRESHOLD else "one_line"
        new_slow = (
            fsh
            if surprise > SURPRISE_THRESHOLD
            else (old["slow_state_hash"] if old else fsh)
        )

        conn.execute(
            _UPSERT_SQL,
            (
                eid,
                stable_composite,
                ts,
                ss,
                fs,
                fsh,
                new_slow,
                surprise,
                fp,
                boot_treatment,
            ),
        )

        results.append(
            SalienceResult(
                entity_id=eid,
                entity_name=entity_map[eid]["name"],
                entity_type=entity_map[eid]["type"],
                salience_score=composite,
                temporal_score=ts,
                structural_score=ss,
                contextual_score=cs,
                frequency_score=fs,
                surprise=surprise,
                boot_treatment=boot_treatment,
                domain=entity_domains.get(eid),
            )
        )

    if recompute_ids:
        conn.commit()

    results.sort(key=lambda r: r.salience_score, reverse=True)
    return results, cache_hits, cache_misses
