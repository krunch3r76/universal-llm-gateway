"""
Cascade rejection for supporting claims.

When a direct claim fails verification, any supporting claims that reference it
via parent_statement_id must also be rejected.

Invariant: forall s in accepted where s.claim_type = "supporting":
    exists d in accepted where d.claim_type = "direct": s.parent_statement_id = d.statement_id
"""

from __future__ import annotations

from universal_logging import get_logger

logger = get_logger(__name__)


def _normalize_text(text: str) -> str:
    """Normalize text for matching: lowercase, collapse whitespace."""
    return " ".join(text.lower().split())


def parent_context_matches_direct(
    parent_context: str | None,
    direct_claim_texts: set[str],
    *,
    substring_threshold: int = 30,
) -> bool:
    """
    Check if parent_context matches any verified direct claim.

    Matching strategies (in order):
    1. Exact match (normalized)
    2. Parent context is substring of direct claim (>=threshold chars)
    3. Direct claim is substring of parent context (>=threshold chars)
    """
    if not parent_context:
        return False

    norm_parent = _normalize_text(parent_context)

    for direct_text in direct_claim_texts:
        norm_direct = _normalize_text(direct_text)

        if norm_parent == norm_direct:
            return True
        if len(norm_parent) >= substring_threshold and norm_parent in norm_direct:
            return True
        if len(norm_direct) >= substring_threshold and norm_direct in norm_parent:
            return True

    return False


def _resolve_merge_target(
    statement_id: str,
    merge_map: dict[str, str] | None,
) -> str:
    """Resolve transitive merges (A->B->C) to canonical statement_id."""
    if not merge_map:
        return statement_id

    current = statement_id
    seen: set[str] = set()
    while current in merge_map:
        if current in seen:
            logger.error("Cascade: cycle detected in merge_map for %s", statement_id)
            return current
        seen.add(current)
        current = merge_map[current]
    return current


def parent_statement_id_matches_direct(
    parent_statement_id: str | None,
    direct_statement_ids: set[str],
    *,
    merge_map: dict[str, str] | None = None,
) -> bool:
    """Check if parent_statement_id resolves to a verified direct claim statement_id."""
    if not parent_statement_id:
        return False
    resolved = _resolve_merge_target(parent_statement_id, merge_map)
    return resolved in direct_statement_ids


def apply_cascade_rejection(
    statements: list[dict],
    *,
    merge_map: dict[str, str] | None = None,
) -> tuple[list[dict], list[str], int]:
    """
    Cascade-reject supporting claims whose parent_statement_id doesn't match
    verified direct claims.

    Modifies statements in-place, removing rejected supporting claims.

    Returns:
        (cascade_rejected_details, rejected_texts, count)
    """
    accepted_direct_ids: set[str] = set()
    accepted_direct_texts: set[str] = set()
    for item in statements:
        if item.get("claim_type") == "direct":
            stmt_id = item.get("statement_id")
            if isinstance(stmt_id, str) and stmt_id:
                accepted_direct_ids.add(stmt_id)
            text = item.get("text")
            if isinstance(text, str) and text:
                accepted_direct_texts.add(text)

    cascade_rejected: list[dict] = []
    rejected_texts: list[str] = []
    surviving: list[dict] = []

    for item in statements:
        claim_type = item.get("claim_type", "direct")

        if claim_type != "supporting":
            surviving.append(item)
            continue

        parent_statement_id = item.get("parent_statement_id")
        resolved_parent_id = (
            _resolve_merge_target(parent_statement_id, merge_map)
            if isinstance(parent_statement_id, str) and parent_statement_id
            else None
        )

        if parent_statement_id_matches_direct(
            parent_statement_id, accepted_direct_ids, merge_map=merge_map
        ):
            surviving.append(item)
        else:
            parent_context = item.get("parent_context")
            used_fallback = False
            if not parent_statement_id and parent_context:
                used_fallback = True
                if parent_context_matches_direct(parent_context, accepted_direct_texts):
                    surviving.append(item)
                    continue

            rejected_texts.append(item["text"])
            cascade_rejected.append(
                {
                    "statement_id": item.get("statement_id"),
                    "text": item["text"],
                    "parent_statement_id": parent_statement_id,
                    "resolved_parent_statement_id": resolved_parent_id,
                    "parent_context": item.get("parent_context"),
                    "reason": "cascade_parent_not_verified",
                    "fallback_used": used_fallback,
                }
            )
            logger.info(
                "Cascade rejection: '%s...' (parent not in verified direct claims)",
                item["text"][:60],
            )

    statements.clear()
    statements.extend(surviving)
    return cascade_rejected, rejected_texts, len(cascade_rejected)
