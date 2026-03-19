#!/usr/bin/env python3
"""Journal-to-Cortex assertion ingest pipeline.

Fetches journal entries with pending changes from journal-bridge,
extracts structured assertions via Claude, and seeds them into Cortex.

Usage:
    python ingest.py            # Process all pending entries
    python ingest.py --dry-run  # Show extractions without seeding
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Any

import httpx
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

logger = logging.getLogger("journal-ingest")

BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://localhost:8200")
BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("INGEST_MODEL", "claude-sonnet-4-20250514")

EXTRACTION_SYSTEM = """\
You are a knowledge extraction system for a personal knowledge graph belonging \
to Kaywan Joseph Mansubi, a PharmD pursuing a legal case involving his parents' estate.

Given new journal content (lines added since last ingestion), extract structured assertions.

RULES:
- Each assertion must be atomic (one fact), faithful (accurate to source), \
and decontextualized (use full names, no pronouns).
- Resolve mentions to existing entities when possible. Use type:slug format.
- Confidence levels: confirmed (directly stated fact), believed (high confidence \
inference), suspected (pattern-based), hypothesized (theory).
- Default to confirmed for direct observations, suspected for behavioral patterns.
- Include evidence: one sentence explaining the basis.
- If the content has no extractable facts, return empty arrays.

Respond ONLY with JSON:
{
  "assertions": [
    {
      "entity_id": "type:slug",
      "entity_name": "Display Name",
      "claim": "The atomic assertion",
      "confidence": "confirmed|believed|suspected|hypothesized",
      "evidence": "Why — one sentence"
    }
  ],
  "new_entities": [
    {
      "id": "type:slug",
      "name": "Display Name",
      "type": "person|organization|event|etc"
    }
  ]
}"""


def _cortex_client() -> httpx.Client:
    return make_sync_client(DEFAULT_CORTEX_URL)


def _bridge_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if BRIDGE_TOKEN:
        headers["Authorization"] = f"Bearer {BRIDGE_TOKEN}"
    return headers


def fetch_pending() -> list[dict[str, Any]]:
    """Get entries pending Cortex ingestion from journal-bridge."""
    with httpx.Client(base_url=BRIDGE_URL, timeout=30.0) as client:
        resp = client.get("/sync/status", headers=_bridge_headers())
        resp.raise_for_status()
    return resp.json().get("pending", [])


def fetch_diff(entry_id: int) -> dict[str, Any] | None:
    """Fetch latest diff for an entry. Returns None if no diff or no changes."""
    with httpx.Client(base_url=BRIDGE_URL, timeout=30.0) as client:
        resp = client.get(
            f"/entries/{entry_id}/diffs/latest", headers=_bridge_headers()
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
    if not data.get("has_changes"):
        return None
    return data


def extract_added_lines(diff_text: str) -> str:
    """Extract only the added lines (+ prefix) from a unified diff."""
    added = []
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    return "\n".join(added)


def fetch_existing_entities() -> list[dict[str, Any]]:
    """Fetch existing Cortex entities for entity resolution context."""
    with _cortex_client() as client:
        resp = client.get("http://localhost/entities", params={"limit": 200})
        resp.raise_for_status()
    return resp.json().get("items", [])


def call_claude(
    added_content: str, entry_id: int, entities: list[dict[str, Any]]
) -> dict[str, Any]:
    """Call Claude to extract structured assertions from added journal content."""
    entities_ctx = "\n".join(f"- {e['id']} ({e['name']})" for e in entities)
    user_msg = (
        f"## Existing entities in the knowledge graph\n{entities_ctx}\n\n"
        f"## New journal content (entry {entry_id})\n{added_content}"
    )

    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": 4096,
            "system": EXTRACTION_SYSTEM,
            "messages": [{"role": "user", "content": user_msg}],
        },
        timeout=60.0,
    )
    resp.raise_for_status()

    text = resp.json()["content"][0]["text"]
    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    text = re.sub(r"\n?```\s*$", "", text.strip())
    return json.loads(text)


def create_entity(entity: dict[str, Any]) -> bool:
    """Create a new entity in Cortex. Returns True on success or conflict (idempotent)."""
    with _cortex_client() as client:
        resp = client.post(
            "http://localhost/entities",
            json={"id": entity["id"], "type": entity["type"], "name": entity["name"]},
        )
    if resp.status_code == 409:
        logger.info("Entity %s already exists, skipping", entity["id"])
        return True
    if resp.status_code >= 400:
        logger.error("Failed to create entity %s: %s", entity["id"], resp.text)
        return False
    logger.info("Created entity: %s (%s)", entity["id"], entity["name"])
    return True


def seed_assertion(assertion: dict[str, Any], entry_id: int) -> bool:
    """Seed a single assertion into Cortex."""
    with _cortex_client() as client:
        resp = client.post(
            "http://localhost/assertions",
            json={
                "entity_id": assertion["entity_id"],
                "claim": assertion["claim"],
                "confidence": assertion["confidence"],
                "evidence": assertion.get("evidence", ""),
                "evidence_uris": [f"journal-bridge:entry:{entry_id}"],
            },
        )
    if resp.status_code >= 400:
        logger.error(
            "Failed to seed assertion for %s: %s", assertion["entity_id"], resp.text
        )
        return False
    logger.info("Seeded: [%s] %s", assertion["entity_id"], assertion["claim"][:80])
    return True


def mark_synced(entry_id: int) -> bool:
    """Mark entry as cortex_ingested in journal-bridge."""
    with httpx.Client(base_url=BRIDGE_URL, timeout=30.0) as client:
        resp = client.patch(f"/entries/{entry_id}/synced", headers=_bridge_headers())
    if resp.status_code >= 400:
        logger.error("Failed to mark entry %d synced: %s", entry_id, resp.text)
        return False
    logger.info("Marked entry %d as synced", entry_id)
    return True


def process_entry(
    entry: dict[str, Any],
    entities: list[dict[str, Any]],
    *,
    dry_run: bool = False,
) -> bool:
    """Process a single pending entry end-to-end. Returns True on success."""
    entry_id = entry["entry_id"]
    logger.info("Processing entry %d...", entry_id)

    diff = fetch_diff(entry_id)
    added = extract_added_lines(diff["diff"]) if diff else ""
    if not added.strip():
        logger.info("Entry %d: no extractable changes, marking synced", entry_id)
        if not dry_run:
            mark_synced(entry_id)
        return True

    logger.info(
        "Entry %d: %d added lines, calling Claude...", entry_id, added.count("\n") + 1
    )

    result = call_claude(added, entry_id, entities)
    assertions = result.get("assertions", [])
    new_entities = result.get("new_entities", [])

    if dry_run:
        logger.info(
            "DRY RUN — entry %d: %d assertions, %d new entities",
            entry_id,
            len(assertions),
            len(new_entities),
        )
        for a in assertions:
            logger.info(
                "  [%s] %s (%s)", a["entity_id"], a["claim"][:80], a["confidence"]
            )
        for e in new_entities:
            logger.info("  NEW ENTITY: %s (%s)", e["id"], e["name"])
        return True

    for ent in new_entities:
        if not create_entity(ent):
            logger.error("Aborting entry %d — entity creation failed", entry_id)
            return False
        entities.append({"id": ent["id"], "name": ent["name"], "type": ent["type"]})

    all_ok = True
    for assertion in assertions:
        if not seed_assertion(assertion, entry_id):
            all_ok = False
    if not all_ok:
        logger.error(
            "Some assertions failed for entry %d — not marking synced", entry_id
        )
        return False

    mark_synced(entry_id)
    return True


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set")
        return 1
    if not BRIDGE_TOKEN:
        logger.warning("BRIDGE_TOKEN not set — requests may fail auth")

    pending = fetch_pending()
    if not pending:
        logger.info("No pending entries")
        return 0

    logger.info("Found %d pending entries", len(pending))

    entities = fetch_existing_entities()
    logger.info("Loaded %d existing entities for context", len(entities))

    ok = 0
    fail = 0
    for entry in pending:
        try:
            if process_entry(entry, entities, dry_run=dry_run):
                ok += 1
            else:
                fail += 1
        except Exception:
            logger.exception("Unexpected error processing entry %d", entry["entry_id"])
            fail += 1

    logger.info("Done: %d succeeded, %d failed", ok, fail)
    return 1 if fail > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
