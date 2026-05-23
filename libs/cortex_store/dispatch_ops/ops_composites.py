"""Cortex Layer 1 composite ops — DB-only atomic writes (Phase 1a of
cortex-graph-projection-and-audit-primitives per v2 plan).

All ops are strictly DB-only (no filesystem mutation inside tx per C1).
Idempotency follows per-op equality table (W3). Path validation uses
_validate_canonical_sandbox_path (W5). register_decision registers the new
`decision:` entity type (S2).

See v2 plan §5 for exact semantics, error codes, and docstring requirements.
"""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from ..db import cortex_conn, query
from ._shared import _validate_canonical_sandbox_path, record
from .ops_entities import _op_entity_create
from .ops_relationships import _op_relationship_create

logger = get_logger("cortex-api.dispatch_ops.composites")


def _op_register_skill_substrate(
    skill_id: str,
    skill_path: str,
    case_id: str | None = None,
    description: str = "",
    trigger_phrases: list[str] | None = None,
    session_id: str | None = None,
    agent: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Atomic DB-only composite: agent_skill: + document: + relationship_create.

    All three writes execute inside a single explicit SQLite transaction so
    partial failures cannot leave orphaned entities (C1 atomicity).

    Idempotency (W3):
    - If agent_skill:<skill_id> exists and matches (name, source_uri canonical,
      description, trigger_phrases as set), return existing with _status="existing".
    - If diverges, return composite_conflict with diff + suggested entity_update.
    - Else create all three rows atomically.

    Uses _validate_canonical_sandbox_path(skill_path, canonical_subdir="agent-skills").
    Emits cortex.composite.registered with entity_ids, composite, status.
    """
    if not skill_id or not skill_path:
        return {"error": "skill_id and skill_path are required"}

    try:
        validated_path = _validate_canonical_sandbox_path(
            skill_path, canonical_subdir="agent-skills", must_be_file=True
        )
    except ValueError as exc:
        return {"error": str(exc), "code": "invalid_skill_path"}

    skill_entity_id = f"agent_skill:{skill_id}"
    document_entity_id = f"document:skill-{skill_id}"

    with cortex_conn() as conn:
        conn.execute("BEGIN")
        try:
            # Idempotency check (W3)
            existing = query(
                conn,
                "SELECT id, name, description, attributes, source_uri FROM entities WHERE id = ?",
                (skill_entity_id,),
            )
            if existing:
                conn.execute("ROLLBACK")
                skill = existing[0]
                attrs = skill.get("attributes") or {}
                current_phrases = set(attrs.get("trigger_phrases") or [])
                requested_phrases = set(trigger_phrases or [])

                if (
                    skill.get("name") == skill_id
                    and str(skill.get("source_uri") or "") == str(validated_path)
                    and (skill.get("description") or "") == (description or "")
                    and current_phrases == requested_phrases
                ):
                    record(
                        "cortex.composite.registered",
                        composite="register_skill_substrate",
                        entity_ids=[skill_entity_id, document_entity_id],
                        status="existing",
                    )
                    return {
                        "skill_id": skill_entity_id,
                        "document_id": document_entity_id,
                        "status": "existing",
                        "_status": "idempotent",
                        "validated_path": str(validated_path),
                    }

                # Conflict
                diff = {
                    "name": [skill.get("name"), skill_id],
                    "source_uri": [skill.get("source_uri"), str(validated_path)],
                    "description": [skill.get("description"), description],
                    "trigger_phrases": [
                        sorted(current_phrases),
                        sorted(requested_phrases),
                    ],
                }
                return {
                    "error": "composite_conflict",
                    "code": "composite_conflict",
                    "message": f"agent_skill:{skill_id} exists but fields diverge",
                    "diff": diff,
                    "suggested": {
                        "op": "entity_update",
                        "entity_id": skill_entity_id,
                        "updates": {
                            "description": description,
                            "attributes": {
                                "trigger_phrases": sorted(requested_phrases)
                            },
                        },
                    },
                }

            # Create — all three writes inside the explicit BEGIN above
            skill_result = _op_entity_create(
                id=skill_entity_id,
                type="agent_skill",
                name=skill_id,
                description=description,
                source_uri=str(validated_path),
                attributes={
                    "trigger_phrases": trigger_phrases or [],
                    "canonical_path": str(validated_path),
                    "lifecycle_status": "registered",
                },
                session_id=session_id,
                agent=agent or "cursor",
            )
            if isinstance(skill_result, dict) and "error" in skill_result:
                conn.execute("ROLLBACK")
                return skill_result

            doc_result = _op_entity_create(
                id=document_entity_id,
                type="document",
                name=skill_id,
                description=description or f"Skill substrate for {skill_id}",
                source_uri=str(validated_path),
                attributes={
                    "lifecycle_status": "registered",
                    "kind": "agent_skill",
                },
                session_id=session_id,
                agent=agent or "cursor",
            )
            if isinstance(doc_result, dict) and "error" in doc_result:
                conn.execute("ROLLBACK")
                return doc_result

            # Wire keystone relationship (per plan edge taxonomy in §3)
            rel_result = _op_relationship_create(
                source_id=skill_entity_id,
                target_id=document_entity_id,
                type_id="keystone_of",
                role="substrate",
                evidence=f"register_skill_substrate({skill_id})",
                session_id=session_id,
                agent=agent or "cursor",
            )
            if (
                isinstance(rel_result, dict)
                and "error" in rel_result
                and "already exists" not in str(rel_result.get("error", ""))
            ):
                conn.execute("ROLLBACK")
                return rel_result

            if case_id:
                case_rel = _op_relationship_create(
                    source_id=case_id if isinstance(case_id, str) else str(case_id),
                    target_id=skill_entity_id,
                    type_id="uses_skill",
                    evidence=f"register_skill_substrate({skill_id})",
                    session_id=session_id,
                    agent=agent or "cursor",
                )
                if (
                    isinstance(case_rel, dict)
                    and "error" in case_rel
                    and "already exists" not in str(case_rel.get("error", ""))
                ):
                    conn.execute("ROLLBACK")
                    return case_rel

            conn.execute("COMMIT")

        except Exception:
            conn.execute("ROLLBACK")
            raise

    record(
        "cortex.composite.registered",
        composite="register_skill_substrate",
        entity_ids=[skill_entity_id, document_entity_id],
        status="created",
    )

    return {
        "skill_id": skill_entity_id,
        "document_id": document_entity_id,
        "status": "created",
        "validated_path": str(validated_path),
        "_next": "use entity_get to verify; audit for gaps; render in Layer 4",
    }


# Additional composites (_op_register_evidence, _op_register_person, etc.)
# will be added in subsequent batches after this skeleton passes gates + registry.

__all__ = ["_op_register_skill_substrate"]
