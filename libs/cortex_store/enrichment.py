"""Assertion enrichment — prospective indexing + event extraction.

Post-write enrichment generates LLM-derived metadata for assertions:
- **Prospective summary**: future-scenario implications for retrieval bridging
- **Event extraction**: structured causal events from claim text

All enrichment is best-effort and non-blocking to the assertion write path.
Failures are logged at WARNING and swallowed.
"""

from __future__ import annotations

import json
import os
import threading
from typing import TYPE_CHECKING

import httpx
from universal_logging import get_logger

if TYPE_CHECKING:
    import sqlite3

logger = get_logger("cortex-api.enrichment")

_ENRICHMENT_ENABLED: set[str] = set()
_raw = os.environ.get("CORTEX_ENRICHMENT_ENABLED", "")
if _raw.strip():
    _ENRICHMENT_ENABLED = {s.strip() for s in _raw.split(",") if s.strip()}

ENRICHMENT_MODEL = os.environ.get("CORTEX_ENRICHMENT_MODEL", "")

STARGATE_URL = "http://localhost:9999"
_REQUEST_TIMEOUT = 30.0


def _get_model() -> str:
    """Resolve the model to use for enrichment calls."""
    if ENRICHMENT_MODEL:
        return ENRICHMENT_MODEL
    return ""


def _chat_completion(system: str, user: str) -> str | None:
    """Call Stargate chat completions endpoint. Returns content or None."""
    model = _get_model()
    if not model:
        logger.warning("No enrichment model configured (CORTEX_ENRICHMENT_MODEL empty)")
        return None

    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 256,
        "temperature": 0.3,
    }

    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
            resp = client.post(
                f"{STARGATE_URL}/v1/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception:
        logger.warning("Enrichment LLM call failed", exc_info=True)
        return None


_PROSPECTIVE_SYSTEM = """\
Cortex is a personal knowledge graph shared by multiple AI agents (web, cursor, oppie). \
Assertions are epistemic claims about entities: people, decisions, services, legal matters, documents, todos.

Confidence taxonomy: confirmed (verified), believed (working assumption), suspected (pattern inference), hypothesized (theory).

Your task: given an assertion, write 1-2 sentences of prospective retrieval cues — \
future questions or scenarios where this assertion matters but the claim text alone would not surface it. \
These cues are embedded alongside the assertion for semantic search during agent boot and session pickup.

Rules:
- Focus on NON-OBVIOUS connections the claim text itself does not contain.
- Reference adjacent domains, downstream consequences, or investigative angles.
- For decisions: what might revisit this choice? For people: what future interactions? \
For legal: what procedural triggers? For services: what failure modes or migrations?
- Keep it dense and specific. No preamble, no hedging."""


def generate_prospective_summary(
    claim: str, entity_id: str, confidence: str
) -> str | None:
    """Generate a future-scenario prospective summary for retrieval bridging."""
    user_prompt = f"Entity: {entity_id}\nConfidence: {confidence}\nClaim: {claim}"
    return _chat_completion(_PROSPECTIVE_SYSTEM, user_prompt)


_EVENTS_SYSTEM = """\
Cortex assertions capture epistemic state across agent sessions. \
Claims often compress multiple causal events into a single sentence. \
When an assertion is superseded, this causal detail is lost.

Your task: decompose the claim into structured events that preserve causal chains.

Return a JSON array of objects:
- "event": what happened or was decided (concise, specific)
- "consequence": what resulted, changed, or may result (downstream effect, not restatement)
- "temporal": when it happened if stated (ISO date, relative phrase, or null)

Rules:
- Only extract events with clear cause→effect structure.
- Separate compound claims into distinct events.
- Return [] if the claim is purely descriptive with no causal content.
- Return ONLY valid JSON. No markdown fences, no commentary."""


def extract_events(claim: str, entity_id: str) -> str | None:
    """Extract structured causal events from claim text as JSON string."""
    user_prompt = f"Entity: {entity_id}\nClaim: {claim}"
    result = _chat_completion(_EVENTS_SYSTEM, user_prompt)
    if result is None:
        return None

    cleaned = result.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        parsed = json.loads(cleaned)
        if not isinstance(parsed, list):
            logger.warning("Event extraction returned non-array: %s", type(parsed))
            return None
        return json.dumps(parsed)
    except json.JSONDecodeError:
        logger.warning("Event extraction returned invalid JSON: %.200s", cleaned)
        return None


def is_enrichment_enabled(kind: str) -> bool:
    """Check whether a specific enrichment kind is enabled."""
    return kind in _ENRICHMENT_ENABLED


def _build_indexed_text(
    claim: str,
    prospective_summary: str | None,
    events_json: str | None,
    entity_id: str,
) -> str:
    """Compose the FTS5 indexed_text from claim + enrichment columns."""
    parts: list[str] = [claim]

    if prospective_summary:
        parts.append(prospective_summary)

    if events_json:
        try:
            events = json.loads(events_json)
            if isinstance(events, list):
                for ev in events:
                    if isinstance(ev, dict):
                        for key in ("event", "consequence", "temporal"):
                            val = ev.get(key)
                            if val:
                                parts.append(str(val))
        except (json.JSONDecodeError, TypeError):
            pass

    parts.append(entity_id)
    return "\n".join(parts)


def reindex_assertion_fts(assertion_id: int) -> None:
    """Rebuild the FTS5 row for a single assertion from its current DB state."""
    from .db import WRITE_LOCK, cortex_conn

    try:
        with WRITE_LOCK, cortex_conn() as conn:
            row = conn.execute(
                "SELECT claim, prospective_summary, events_json, entity_id "
                "FROM assertions WHERE id = ?",
                (assertion_id,),
            ).fetchone()
            if not row:
                return
            claim, prospective, events, eid = row
            indexed = _build_indexed_text(claim, prospective, events, eid)

            conn.execute(
                "DELETE FROM assertions_fts WHERE assertion_id = ?",
                (assertion_id,),
            )
            conn.execute(
                "INSERT INTO assertions_fts (assertion_id, entity_id, indexed_text) "
                "VALUES (?, ?, ?)",
                (assertion_id, eid, indexed),
            )
            conn.commit()
    except Exception:
        logger.warning(
            "FTS reindex failed for assertion %d", assertion_id, exc_info=True
        )


def _update_assertion_field(assertion_id: int, field: str, value: str) -> None:
    """Update a single enrichment field on an assertion row."""
    from .db import WRITE_LOCK, cortex_conn

    try:
        with WRITE_LOCK, cortex_conn() as conn:
            conn.execute(
                f"UPDATE assertions SET {field} = ? WHERE id = ?",
                (value, assertion_id),
            )
            conn.commit()
    except Exception:
        logger.warning(
            "Failed to update %s for assertion %d", field, assertion_id, exc_info=True
        )


def enrich_assertion(
    assertion_id: int,
    claim: str,
    entity_id: str,
    confidence: str,
    kinds: set[str] | None = None,
) -> dict[str, str | None]:
    """Run requested enrichments on an assertion. Returns results dict.

    *kinds* defaults to globally enabled enrichments if None.
    """
    if kinds is None:
        kinds = _ENRICHMENT_ENABLED

    results: dict[str, str | None] = {}

    if "prospective" in kinds:
        summary = generate_prospective_summary(claim, entity_id, confidence)
        if summary:
            _update_assertion_field(assertion_id, "prospective_summary", summary)
        results["prospective_summary"] = summary

    if "events" in kinds:
        events = extract_events(claim, entity_id)
        if events:
            _update_assertion_field(assertion_id, "events_json", events)
        results["events_json"] = events

    reindex_assertion_fts(assertion_id)

    return results


def enrich_background(
    assertion_id: int,
    claim: str,
    entity_id: str,
    confidence: str,
) -> None:
    """Fire enrichment in a daemon thread — never blocks the write path."""
    if not _ENRICHMENT_ENABLED:
        return

    def _run() -> None:
        try:
            enrich_assertion(assertion_id, claim, entity_id, confidence)
        except Exception:
            logger.warning(
                "Background enrichment failed for assertion %d",
                assertion_id,
                exc_info=True,
            )

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def enrich_old_assertion_events(conn: sqlite3.Connection, assertion_id: int) -> None:
    """Enrich an about-to-be-superseded assertion with event extraction if missing.

    Called during supersede to preserve causal structure before compression.
    Runs synchronously in a daemon thread to avoid blocking the write path.
    """
    if not is_enrichment_enabled("events"):
        return

    from .db import query

    rows = query(
        conn,
        "SELECT claim, entity_id, events_json FROM assertions WHERE id = ?",
        (assertion_id,),
    )
    if not rows or rows[0].get("events_json"):
        return

    row = rows[0]

    def _run() -> None:
        try:
            events = extract_events(row["claim"], row["entity_id"])
            if events:
                _update_assertion_field(assertion_id, "events_json", events)
        except Exception:
            logger.warning(
                "Supersede event enrichment failed for assertion %d",
                assertion_id,
                exc_info=True,
            )

    t = threading.Thread(target=_run, daemon=True)
    t.start()
