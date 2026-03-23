"""Component scoring functions for entity salience (v2.2 §5.1–§5.4, §10.5).

Batch SQL queries + NetworkX graph analysis. Each function computes one
salience dimension across all entities in a single pass.
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
from datetime import UTC, datetime

import networkx as nx

from src.db import query

ALPHA_LEGAL = 0.005  # ~139 days
ALPHA_PROJECT = 0.02  # ~35 days
ALPHA_PERSONAL = 0.01  # ~69 days
ALPHA_SYSTEM = 0.05  # ~14 days
ALPHA_DEFAULT = 0.02

_DOMAIN_ALPHA: dict[str, float] = {
    "legal": ALPHA_LEGAL,
    "personal": ALPHA_PERSONAL,
    "system": ALPHA_SYSTEM,
    "events": ALPHA_SYSTEM,
    "observability": ALPHA_SYSTEM,
    "tooling": ALPHA_SYSTEM,
}

EVENT_PARTICIPATION_TYPES = frozenset(
    {"participant", "subject_of", "object_of", "location_of", "role_in", "triggers"}
)


def alpha_for_domain(raw_domain: str | None) -> float:
    """Map an entity's domain string to its temporal decay rate."""
    if not raw_domain:
        return ALPHA_DEFAULT
    for token in raw_domain.lower().replace(",", " ").split():
        if token in _DOMAIN_ALPHA:
            return _DOMAIN_ALPHA[token]
    return ALPHA_PROJECT


def days_since(ts_str: str | None, now: datetime) -> float:
    """Parse an ISO-ish timestamp and return days elapsed from *now*."""
    if not ts_str:
        return 365.0
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        delta = (now - ts).total_seconds()
        return max(delta / 86400.0, 0.0)
    except (ValueError, TypeError):
        return 365.0


# ---------------------------------------------------------------------------
# Batch scoring
# ---------------------------------------------------------------------------


def batch_temporal(
    conn: sqlite3.Connection,
    entity_domains: dict[str, str | None],
    now: datetime,
) -> dict[str, float]:
    """Temporal salience via exponential decay from last activity."""
    rows = query(
        conn,
        """
        SELECT entity_id,
               MAX(COALESCE(observed_at, valid_from, created_at)) AS last_activity
        FROM assertions
        GROUP BY entity_id
        """,
    )
    last_activity: dict[str, str | None] = {
        r["entity_id"]: r["last_activity"] for r in rows
    }

    rel_rows = query(
        conn,
        """
        SELECT from_entity AS eid, MAX(created_at) AS last_rel
        FROM relationships GROUP BY from_entity
        UNION ALL
        SELECT to_entity AS eid, MAX(created_at) AS last_rel
        FROM relationships GROUP BY to_entity
        """,
    )
    for r in rel_rows:
        eid = r["eid"]
        existing = last_activity.get(eid)
        if existing is None or (r["last_rel"] and r["last_rel"] > existing):
            last_activity[eid] = r["last_rel"]

    scores: dict[str, float] = {}
    for eid, domain in entity_domains.items():
        alpha = alpha_for_domain(domain)
        days = days_since(last_activity.get(eid), now)
        scores[eid] = math.exp(-alpha * days)
    return scores


def batch_structural(
    conn: sqlite3.Connection,
    entity_ids: list[str],
) -> dict[str, float]:
    """Structural salience: log-count of connections + betweenness centrality bonus."""
    assertion_counts: dict[str, int] = {}
    for r in query(
        conn, "SELECT entity_id, COUNT(*) AS cnt FROM assertions GROUP BY entity_id"
    ):
        assertion_counts[r["entity_id"]] = r["cnt"]

    rel_counts: dict[str, int] = {}
    participation_counts: dict[str, int] = {}
    all_edges: list[tuple[str, str]] = []

    for r in query(
        conn,
        "SELECT from_entity, to_entity, type FROM relationships",
    ):
        fe, te, rtype = r["from_entity"], r["to_entity"], r["type"]
        rel_counts[fe] = rel_counts.get(fe, 0) + 1
        rel_counts[te] = rel_counts.get(te, 0) + 1
        all_edges.append((fe, te))
        if rtype in EVENT_PARTICIPATION_TYPES:
            participation_counts[fe] = participation_counts.get(fe, 0) + 1
            participation_counts[te] = participation_counts.get(te, 0) + 1

    graph = nx.Graph()
    graph.add_nodes_from(entity_ids)
    graph.add_edges_from(all_edges)
    centrality = nx.betweenness_centrality(graph)

    scores: dict[str, float] = {}
    for eid in entity_ids:
        ac = assertion_counts.get(eid, 0)
        rc = rel_counts.get(eid, 0)
        pc = participation_counts.get(eid, 0)
        base = math.log(1 + ac + rc + pc)
        bonus = centrality.get(eid, 0.0) * 5.0
        scores[eid] = base + bonus
    return scores


def batch_frequency(
    conn: sqlite3.Connection,
    entity_ids: list[str],
) -> dict[str, float]:
    """Mention dispersion across session journals + surface form count, normalized 0–1."""
    journals = query(
        conn, "SELECT id, summary, decisions, open_items FROM session_journals"
    )
    mention_counts: dict[str, int] = {}
    for eid in entity_ids:
        slug = eid.split(":", 1)[-1] if ":" in eid else eid
        count = 0
        for j in journals:
            text = " ".join(
                filter(None, [j["summary"], j["decisions"], j["open_items"]])
            )
            if slug in text.lower():
                count += 1
        mention_counts[eid] = count

    sf_rows = query(
        conn,
        "SELECT entity_id, COUNT(*) AS cnt FROM surface_forms GROUP BY entity_id",
    )
    sf_counts: dict[str, int] = {r["entity_id"]: r["cnt"] for r in sf_rows}

    raw: dict[str, float] = {}
    for eid in entity_ids:
        raw[eid] = float(mention_counts.get(eid, 0) + sf_counts.get(eid, 0))

    max_val = max(raw.values()) if raw else 1.0
    if max_val == 0.0:
        return {eid: 0.0 for eid in entity_ids}
    return {eid: v / max_val for eid, v in raw.items()}


CONTEXTUAL_SPREAD_BONUS = 0.3
CONTEXTUAL_K_DAMPING = 0.5
CONTEXTUAL_SESSION_WINDOW_HOURS = 2
ACCESS_LOG_RETENTION_DAYS = 30


def batch_contextual(
    conn: sqlite3.Connection,
    entity_ids: list[str],
    agent: str,
    session_id: str | None = None,
    t_now: datetime | None = None,
) -> dict[str, float]:
    """Contextual salience from entity access log: count-weighted log(1+count) + spread bonus.

    Confidence damping ``1 - exp(-k * count)`` applied before normalization
    prevents single-access entities from inflating to 1.0.

    Count-weighted scoring is the component most likely to need post-Phase 3
    tuning.  If count-bias appears (entities queried repeatedly because they
    are confusing score higher than entities understood in one look),
    exponential recency decay with min-max normalization is the documented
    fallback.
    """
    if t_now is None:
        t_now = datetime.now(UTC)

    if session_id:
        rows = query(
            conn,
            "SELECT entity_id, COUNT(*) AS access_count, "
            "MIN(created_at) AS first_access, MAX(created_at) AS last_access "
            "FROM entity_access_log WHERE agent = ? AND session_id = ? "
            "GROUP BY entity_id",
            (agent, session_id),
        )
    else:
        rows = query(
            conn,
            "SELECT entity_id, COUNT(*) AS access_count, "
            "MIN(created_at) AS first_access, MAX(created_at) AS last_access "
            "FROM entity_access_log WHERE agent = ? "
            "AND created_at > datetime('now', ?) GROUP BY entity_id",
            (agent, f"-{CONTEXTUAL_SESSION_WINDOW_HOURS} hours"),
        )

    if not rows:
        return {eid: 0.0 for eid in entity_ids}

    id_set = set(entity_ids)
    raw_scores: dict[str, float] = {}
    for r in rows:
        eid = r["entity_id"]
        if eid not in id_set:
            continue

        intensity = math.log(1 + r["access_count"])

        spread = 0.0
        if r["access_count"] >= 2 and r["first_access"] and r["last_access"]:
            first_age = days_since(r["first_access"], t_now)
            last_age = days_since(r["last_access"], t_now)
            span_minutes = (first_age - last_age) * 1440.0
            if span_minutes > 0:
                spread = min(span_minutes / 120.0, 1.0)

        score = intensity * (1.0 + CONTEXTUAL_SPREAD_BONUS * spread)
        confidence = 1.0 - math.exp(-CONTEXTUAL_K_DAMPING * r["access_count"])
        raw_scores[eid] = score * confidence

    max_val = max(raw_scores.values()) if raw_scores else 1.0
    if max_val == 0:
        return {eid: 0.0 for eid in entity_ids}

    return {eid: raw_scores.get(eid, 0.0) / max_val for eid in entity_ids}


def compact_access_log(
    conn: sqlite3.Connection, retention_days: int = ACCESS_LOG_RETENTION_DAYS
) -> int:
    """Aggregate old access log entries into weekly summaries, then purge.

    Returns number of purged raw records.
    """
    threshold = f"-{retention_days} days"
    conn.execute(
        "INSERT INTO entity_access_summary "
        "(entity_id, agent, week_start, agent_access_count, "
        "boot_access_count, session_count) "
        "SELECT entity_id, agent, "
        "date(created_at, 'weekday 1', '-7 days') AS week_start, "
        "SUM(CASE WHEN source = 'agent' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN source = 'boot' THEN 1 ELSE 0 END), "
        "COUNT(DISTINCT session_id) "
        "FROM entity_access_log WHERE created_at < datetime('now', ?) "
        "GROUP BY entity_id, agent, week_start "
        "ON CONFLICT(entity_id, agent, week_start) DO UPDATE SET "
        "agent_access_count = entity_access_summary.agent_access_count "
        "+ excluded.agent_access_count, "
        "boot_access_count = entity_access_summary.boot_access_count "
        "+ excluded.boot_access_count, "
        "session_count = MAX(entity_access_summary.session_count, "
        "excluded.session_count)",
        (threshold,),
    )
    cur = conn.execute(
        "DELETE FROM entity_access_log WHERE created_at < datetime('now', ?)",
        (threshold,),
    )
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Fingerprint + EST hashing
# ---------------------------------------------------------------------------


def entity_fingerprint(conn: sqlite3.Connection, entity_id: str) -> str:
    """SHA-256 prefix of sorted assertion + relationship IDs for cache invalidation."""
    a_ids = sorted(
        r["id"]
        for r in query(
            conn, "SELECT id FROM assertions WHERE entity_id = ?", (entity_id,)
        )
    )
    r_ids = sorted(
        r["id"]
        for r in query(
            conn,
            "SELECT id FROM relationships WHERE from_entity = ? OR to_entity = ?",
            (entity_id, entity_id),
        )
    )
    content = f"{a_ids}|{r_ids}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def fast_state_hash(temporal: float, structural: float, frequency: float) -> str:
    """Hash of component scores for EST fast-track comparison."""
    return hashlib.sha256(
        f"{temporal:.6f}|{structural:.6f}|{frequency:.6f}".encode()
    ).hexdigest()[:16]
