"""Core ingestion logic: parsed financial statement → Cortex entities + assertions.

Orchestrates entity creation (account, org, statement), relationship wiring,
temporally scoped assertion seeding, and idempotency via content_hash.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp_events import record

from ._cortex_relay import _cx
from ._finance_assertions import build_assertions
from ._finance_schemas import (
    VALID_TYPES,
    extract_account_suffix,
    extract_issuer_name,
    extract_period,
    resolve_issuer_slug,
)

logger = logging.getLogger(__name__)

_FILES_ROOT = Path("/data/files")


def _content_hash(abs_path: Path) -> str | None:
    """SHA-256 of a local file. Returns None if the file doesn't exist."""
    if not abs_path.is_file():
        return None
    h = hashlib.sha256()
    with open(abs_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _entity_exists(entity_id: str) -> dict[str, Any] | None:
    """Fetch an entity by ID. Returns None if not found."""
    result = _cx("GET", f"/entities/{entity_id}")
    if "error" in result:
        return None
    return result


def _create_or_update_entity(
    entity_id: str,
    entity_type: str,
    name: str,
    *,
    description: str | None = None,
    attributes: dict[str, Any] | None = None,
    aliases: list[str] | None = None,
    source_uri: str | None = None,
    content_hash: str | None = None,
    update_if_exists: bool = True,
) -> dict[str, Any]:
    """Create entity if missing; optionally update if it already exists."""
    existing = _entity_exists(entity_id)
    if existing is not None:
        if not update_if_exists:
            return existing
        patch: dict[str, Any] = {}
        if attributes is not None:
            patch["attributes"] = attributes
        if source_uri is not None:
            patch["source_uri"] = source_uri
        if content_hash is not None:
            patch["content_hash"] = content_hash
        return _cx("PATCH", f"/entities/{entity_id}", patch) if patch else existing

    body: dict[str, Any] = {"id": entity_id, "type": entity_type, "name": name}
    if description:
        body["description"] = description
    if attributes:
        body["attributes"] = attributes
    if aliases:
        body["aliases"] = aliases
    if source_uri:
        body["source_uri"] = source_uri
    if content_hash:
        body["content_hash"] = content_hash
    return _cx("POST", "/entities", body)


def _seed_assertion(
    entity_id: str,
    claim: str,
    *,
    valid_from: str | None = None,
    valid_until: str | None = None,
    evidence: str,
    evidence_uris: list[str],
) -> dict[str, Any]:
    """Create a confirmed assertion with temporal scoping."""
    body: dict[str, Any] = {
        "entity_id": entity_id,
        "claim": claim,
        "confidence": "confirmed",
        "evidence": evidence,
        "evidence_uris": evidence_uris,
        "derivation_type": "compression",
        "confidence_score": 0.95,
        "observed_at": datetime.now(UTC).isoformat(),
    }
    if valid_from:
        body["valid_from"] = valid_from
    if valid_until:
        body["valid_until"] = valid_until
    return _cx("POST", "/assertions", body)


def _build_entity_ids(
    issuer_slug: str,
    statement_type: str,
    acct_suffix: str,
    stmt_date: str,
) -> tuple[str, str, str]:
    """Build (account_entity_id, org_entity_id, statement_entity_id)."""
    parts = [issuer_slug, statement_type]
    if acct_suffix:
        parts.append(acct_suffix)
    account_id = f"account:{'-'.join(parts)}"
    org_id = f"org:{issuer_slug}"
    stmt_parts = parts + [stmt_date]
    statement_id = f"statement:{'-'.join(stmt_parts)}"
    return account_id, org_id, statement_id


def _display_name(issuer: str, stype: str, suffix: str) -> str:
    """Human-readable account display name."""
    label = stype.replace("_", " ").title()
    if suffix:
        return f"{issuer} {label} \u00b7\u00b7\u00b7{suffix}"
    return f"{issuer} {label}"


def ingest_statement(
    parsed: dict[str, Any],
    statement_type: str,
    pdf_path: str,
) -> dict[str, Any]:
    """Ingest a parsed financial statement into Cortex.

    Creates account, org, and statement entities, wires the issued_by
    relationship, and seeds temporally scoped assertions on the account.
    Idempotent via content_hash on the statement entity.
    """
    if statement_type not in VALID_TYPES:
        return {
            "status": "error",
            "error": f"Invalid statement_type: {statement_type!r}",
        }

    issuer_name = extract_issuer_name(parsed, statement_type)
    if not issuer_name:
        return {"status": "error", "error": "Cannot determine issuer from parsed data"}

    issuer_slug = resolve_issuer_slug(issuer_name)
    acct_suffix = extract_account_suffix(parsed, statement_type)
    _, period_end = extract_period(parsed, statement_type)
    stmt_date = period_end or parsed.get("statement_date", "")
    if not stmt_date:
        return {"status": "error", "error": "Cannot determine statement date"}

    account_eid, org_eid, statement_eid = _build_entity_ids(
        issuer_slug, statement_type, acct_suffix, stmt_date
    )

    abs_pdf = _FILES_ROOT / pdf_path.lstrip("/")
    chash = _content_hash(abs_pdf)

    existing_stmt = _entity_exists(statement_eid)
    if existing_stmt is not None:
        existing_hash = existing_stmt.get("content_hash")
        if existing_hash and existing_hash == chash:
            return {
                "status": "already_ingested",
                "account_entity_id": account_eid,
                "statement_entity_id": statement_eid,
                "org_entity_id": org_eid,
            }
        logger.info("Re-ingesting %s (content hash changed)", statement_eid)

    acct_attrs: dict[str, Any] = {
        "issuer": issuer_name,
        "account_type": statement_type,
    }
    if acct_suffix:
        acct_attrs["last4"] = acct_suffix
    if parsed.get("credit_limit") is not None:
        acct_attrs["credit_limit"] = parsed["credit_limit"]
    if parsed.get("interest_rates"):
        acct_attrs["interest_rates"] = parsed["interest_rates"]

    _create_or_update_entity(
        account_eid,
        "account",
        _display_name(issuer_name, statement_type, acct_suffix),
        attributes=acct_attrs,
        source_uri=pdf_path,
    )
    _create_or_update_entity(
        org_eid,
        "organization",
        issuer_name,
        aliases=[issuer_name],
        update_if_exists=False,
    )

    rel_result = _cx(
        "POST",
        "/relationships",
        {
            "source_id": account_eid,
            "target_id": org_eid,
            "type_id": "issued_by",
        },
    )
    rels_created = 0 if "error" in rel_result else 1

    stmt_body: dict[str, Any] = {
        "id": statement_eid,
        "type": "statement",
        "name": f"{issuer_name} {statement_type.replace('_', ' ')} statement {stmt_date}",
        "attributes": parsed,
        "source_uri": pdf_path,
    }
    if chash:
        stmt_body["content_hash"] = chash

    if existing_stmt is not None:
        _cx(
            "PATCH",
            f"/entities/{statement_eid}",
            {k: v for k, v in stmt_body.items() if k not in ("id", "type")},
        )
    else:
        _cx("POST", "/entities", stmt_body)

    evidence = f"Extracted from {Path(pdf_path).name} via finance pipeline Phase 2"
    evidence_uris = [pdf_path]
    assertion_defs = build_assertions(parsed, statement_type)
    assertions_created = 0
    for adef in assertion_defs:
        result = _seed_assertion(
            account_eid,
            adef["claim"],
            valid_from=adef.get("valid_from"),
            valid_until=adef.get("valid_until"),
            evidence=evidence,
            evidence_uris=evidence_uris,
        )
        if "error" not in result:
            assertions_created += 1

    record(
        "mcp.finance.ingest.completed",
        account=account_eid,
        statement=statement_eid,
        org=org_eid,
        assertions=assertions_created,
        relationships=rels_created,
    )
    return {
        "status": "ingested",
        "account_entity_id": account_eid,
        "statement_entity_id": statement_eid,
        "org_entity_id": org_eid,
        "assertions_created": assertions_created,
        "relationships_created": rels_created,
        "parsed": parsed,
    }
