"""Finance reconciliation tool: LLM-native comparison of records.txt against Cortex.

Reads a records.txt file, fetches account entities and assertions from Cortex,
then uses an LLM to perform entity resolution and semantic reconciliation.
Deterministic entity-ID generation is replaced by LLM matching against the
live Cortex entity list.
"""

from __future__ import annotations

import json as _json
import logging
import re
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

from ._cortex_relay import _cx
from ._file_helpers import resolve_files_path
from ._finance_records import BlockRecord, _parse_blocks
from .llm import _call_anthropic

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-20250514"
_MAX_TOKENS = 8192
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)


def _parse_llm_json(raw: str) -> Any:
    """Strip markdown fences and parse JSON from LLM output."""
    text = raw.strip()
    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()
    return _json.loads(text)


def _extract_text(result: dict[str, Any]) -> str:
    """Extract text content from an Anthropic Messages API response."""
    return "".join(
        b.get("text", "") for b in result.get("content", []) if b.get("type") == "text"
    )


def _fetch_account_entities() -> list[dict[str, Any]]:
    """Fetch all account-type entities from Cortex."""
    resp = _cx("GET", "/entities?type=account&limit=200")
    if "error" in resp:
        logger.error("Failed to fetch account entities: %s", resp["error"])
        return []
    if isinstance(resp, list):
        return resp
    return resp.get("entities", resp.get("items", []))


def _fetch_assertions(entity_id: str) -> list[dict[str, Any]]:
    """Fetch confirmed assertions for a single entity."""
    resp = _cx(
        "GET", f"/assertions?entity_id={entity_id}&confidence=confirmed&limit=50"
    )
    if "error" in resp:
        logger.warning(
            "Failed to fetch assertions for %s: %s", entity_id, resp["error"]
        )
        return []
    return resp if isinstance(resp, list) else []


def _blocks_to_dicts(blocks: list[BlockRecord]) -> list[dict[str, Any]]:
    """Convert BlockRecord list to serializable dicts for the LLM prompt."""
    return [
        {"index": i, "issuer": b.issuer, "fields": b.fields, "line": b.line_start}
        for i, b in enumerate(blocks)
    ]


def _entity_resolution_prompt(
    blocks: list[dict[str, Any]],
    entities: list[dict[str, Any]],
) -> str:
    """Build the entity resolution prompt."""
    entity_summary = [
        {
            "id": e.get("id", ""),
            "name": e.get("name", ""),
            "description": e.get("description", ""),
        }
        for e in entities
    ]
    return (
        "You are reconciling financial records against a Cortex knowledge base.\n\n"
        "## Cortex account entities\n"
        f"```json\n{_json.dumps(entity_summary, indent=2)}\n```\n\n"
        "## Record blocks from records.txt\n"
        f"```json\n{_json.dumps(blocks, indent=2)}\n```\n\n"
        "For each record block, determine which Cortex entity it corresponds to.\n"
        "Match by issuer name, account number suffix, and account type.\n\n"
        "Return a JSON array with one object per block:\n"
        "```json\n"
        "[\n"
        "  {\n"
        '    "block_index": 0,\n'
        '    "issuer": "original issuer string",\n'
        '    "matched_entity_id": "account:xxx" or null,\n'
        '    "match_confidence": "high" | "medium" | "low",\n'
        '    "reasoning": "brief explanation"\n'
        "  }\n"
        "]\n"
        "```\n\n"
        "Rules:\n"
        "- Match entity IDs from the Cortex list ONLY. Do not invent IDs.\n"
        "- If no entity matches, set matched_entity_id to null.\n"
        "- Account numbers ending in the same digits strongly indicate a match.\n"
        "- Return ONLY the JSON array, no other text."
    )


def _reconciliation_prompt(
    entity_id: str,
    entity_name: str,
    block_fields: dict[str, str],
    assertions: list[dict[str, Any]],
) -> str:
    """Build the semantic reconciliation prompt for one entity."""
    assertion_summary = [
        {
            "id": a.get("id"),
            "claim": a.get("claim", ""),
            "valid_from": a.get("valid_from"),
            "valid_until": a.get("valid_until"),
        }
        for a in assertions
    ]
    return (
        "You are reconciling a financial record against Cortex assertions.\n\n"
        f"## Entity: {entity_id} ({entity_name})\n\n"
        "## Record fields from records.txt\n"
        f"```json\n{_json.dumps(block_fields, indent=2)}\n```\n\n"
        "## Current confirmed assertions in Cortex\n"
        f"```json\n{_json.dumps(assertion_summary, indent=2)}\n```\n\n"
        "Compare each record field against the assertions. For each field, determine:\n"
        "- **match**: assertion confirms the record value\n"
        "- **discrepancy**: assertion exists but value differs\n"
        "- **missing**: record has data but no corresponding assertion\n"
        "- **stale**: assertion exists but record shows a newer/updated value\n\n"
        "Return a JSON array:\n"
        "```json\n"
        "[\n"
        "  {\n"
        '    "field": "amount_due",\n'
        '    "record_value": "$226.00",\n'
        '    "status": "match",\n'
        '    "assertion_id": 123,\n'
        '    "assertion_claim": "Minimum payment $226.00 due 2026-03-27",\n'
        '    "reasoning": "brief explanation"\n'
        "  }\n"
        "]\n"
        "```\n\n"
        "For discrepancies, include both record_value and actual_value from the assertion.\n"
        "Skip the 'type' and 'account_number' fields — only reconcile financial values.\n"
        "Return ONLY the JSON array, no other text."
    )


def _llm_entity_resolution(
    blocks: list[BlockRecord],
    entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Call LLM to resolve record blocks to Cortex entities."""
    block_dicts = _blocks_to_dicts(blocks)
    prompt = _entity_resolution_prompt(block_dicts, entities)
    payload = {
        "model": _MODEL,
        "max_tokens": _MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
        "system": "You are a financial data reconciliation engine. Return only valid JSON.",
    }
    result = _call_anthropic(payload, requested_model=_MODEL)
    if "error" in result:
        logger.error("Entity resolution LLM call failed: %s", result["error"])
        return [
            {"block_index": i, "error": result["error"]} for i in range(len(blocks))
        ]

    raw = _extract_text(result)
    try:
        return _parse_llm_json(raw)
    except (_json.JSONDecodeError, ValueError) as exc:
        logger.error("Entity resolution JSON parse failed: %s", exc)
        return [
            {"block_index": i, "error": f"JSON parse failed: {exc}"}
            for i in range(len(blocks))
        ]


def _llm_reconcile_entity(
    entity_id: str,
    entity_name: str,
    block_fields: dict[str, str],
    assertions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Call LLM to reconcile one entity's record fields against assertions."""
    prompt = _reconciliation_prompt(entity_id, entity_name, block_fields, assertions)
    payload = {
        "model": _MODEL,
        "max_tokens": _MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
        "system": "You are a financial data reconciliation engine. Return only valid JSON.",
    }
    result = _call_anthropic(payload, requested_model=_MODEL)
    if "error" in result:
        logger.error(
            "Reconciliation LLM call failed for %s: %s", entity_id, result["error"]
        )
        return [{"entity_id": entity_id, "error": result["error"]}]

    raw = _extract_text(result)
    try:
        return _parse_llm_json(raw)
    except (_json.JSONDecodeError, ValueError) as exc:
        logger.error("Reconciliation JSON parse failed for %s: %s", entity_id, exc)
        return [{"entity_id": entity_id, "error": f"JSON parse failed: {exc}"}]


def register_finance_reconcile_tools(mcp: FastMCP) -> None:
    """Register the finance_reconcile tool."""

    @mcp.tool()
    def finance_reconcile(
        path: str,
    ) -> dict[str, Any]:
        """Reconcile a records.txt file against Cortex assertions via LLM.

        Use when: verifying that ingested financial data matches expected
        records. Parses a records.txt file (indented-block format), queries
        Cortex for account entities and their assertions, then uses an LLM
        to match records to entities and compare values semantically.

        records.txt format (indented blocks):
          Issuer Name Statement Date MM/DD/YYYY
              Type: credit_card
              Account number: XXXX-XXXX-XXXX-1234
              Amount due: $226.00
              Balance: $5,432.10

        Returns a structured report with entity matches, value comparisons
        (match/discrepancy/missing/stale), and unmatched records.

        Args:
            path: records.txt path relative to /data/files/ sandbox.
        """
        t0 = monotonic_now()
        record("mcp.finance.reconcile.called", path=path)

        abs_path = resolve_files_path(path)
        if not abs_path.exists():
            raise FileNotFoundError(f"Records file not found: {path!r}")

        content = abs_path.read_text(encoding="utf-8")
        blocks, parse_errors = _parse_blocks(content)

        if not blocks:
            return {
                "path": path,
                "error": "No record blocks found in file",
                "parse_errors": parse_errors,
            }

        entities = _fetch_account_entities()
        if not entities:
            return {
                "path": path,
                "error": "No account entities found in Cortex — nothing to reconcile against",
                "blocks_parsed": len(blocks),
            }

        record(
            "mcp.finance.reconcile.context",
            blocks=len(blocks),
            entities=len(entities),
        )

        # Step 1: LLM entity resolution
        resolution = _llm_entity_resolution(blocks, entities)
        entity_map = {e.get("id", ""): e for e in entities}

        matched: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []
        resolution_errors: list[dict[str, Any]] = []

        for entry in resolution:
            if "error" in entry:
                resolution_errors.append(entry)
                continue
            eid = entry.get("matched_entity_id")
            if eid and eid in entity_map:
                matched.append(entry)
            else:
                unmatched.append(entry)

        # Step 2: fetch assertions + LLM reconciliation per matched entity
        reconciliation: list[dict[str, Any]] = []
        for match in matched:
            idx = match.get("block_index", 0)
            eid = match["matched_entity_id"]
            entity = entity_map[eid]
            block = blocks[idx] if idx < len(blocks) else None
            if block is None:
                continue

            assertions = _fetch_assertions(eid)
            fields_result = _llm_reconcile_entity(
                eid,
                entity.get("name", eid),
                block.fields,
                assertions,
            )
            reconciliation.append(
                {
                    "entity_id": eid,
                    "entity_name": entity.get("name", ""),
                    "issuer": match.get("issuer", ""),
                    "match_confidence": match.get("match_confidence", ""),
                    "match_reasoning": match.get("reasoning", ""),
                    "assertions_checked": len(assertions),
                    "fields": fields_result,
                }
            )

        # Summarize
        all_fields = [
            f
            for r in reconciliation
            for f in r.get("fields", [])
            if isinstance(f, dict)
        ]
        status_counts = {
            "match": 0,
            "discrepancy": 0,
            "missing": 0,
            "stale": 0,
            "error": 0,
        }
        for f in all_fields:
            s = f.get("status", "error")
            status_counts[s] = status_counts.get(s, 0) + 1

        elapsed = monotonic_now() - t0
        record(
            "mcp.finance.reconcile.completed",
            path=path,
            blocks=len(blocks),
            matched=len(matched),
            unmatched=len(unmatched),
            duration_ms=round(elapsed),
            **status_counts,
        )

        return {
            "path": path,
            "summary": {
                "total_blocks": len(blocks),
                "entities_matched": len(matched),
                "entities_unmatched": len(unmatched),
                "resolution_errors": len(resolution_errors),
                "parse_errors": len(parse_errors),
                **status_counts,
            },
            "reconciliation": reconciliation,
            "unmatched": unmatched,
            "resolution_errors": resolution_errors,
            "parse_errors": parse_errors,
        }
