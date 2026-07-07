"""Cortex Layer 1 composite ops — DB-only atomic writes (Phase 1a of
cortex-graph-projection-and-audit-primitives per v2 plan).

All ops are strictly DB-only (no filesystem mutation inside tx per C1).
Idempotency follows per-op equality table (W3). register_skill_substrate path
validation uses _validate_skill_registration_path (workspace SOT). Other ops
may use _validate_canonical_sandbox_path (W5). register_decision registers the new
`decision:` entity type (S2).

See v2 plan §5 for exact semantics, error codes, and docstring requirements.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException
from universal_logging import get_logger

from ..db import WRITE_LOCK, cortex_conn, json_decode, query
from ..entity_crud import create_entity_impl
from ..models import RelationshipCreate
from ..routes.relationships import create_relationship_on_conn
from ._shared import (
    _compute_content_hash,
    _validate_skill_registration_path,
    record,
)
from .ops_entities import _op_entity_create
from .ops_relationships import _op_relationship_create

logger = get_logger("cortex-api.dispatch_ops.composites")

_KNOWN_SKILL_CLASSES = frozenset(
    {"tool_manual", "protocol", "matter_playbook", "discipline"}
)
_VALID_TOOL_EXPOSURES = frozenset({"primary", "overflow", "private"})


def _validate_skill_binding(skill_binding: dict | None) -> dict | None:
    """Shape-only write-path checks for skill_binding (no canonical.yaml lookup)."""
    if skill_binding is None:
        return None

    skill_class = skill_binding.get("skill_class")
    if not isinstance(skill_class, str) or not skill_class:
        return {
            "error": "skill_binding.skill_class must be a non-empty string",
            "code": "skill_binding_invalid",
        }

    tool_binding = skill_binding.get("tool_binding")
    if skill_class == "tool_manual" and tool_binding is None:
        return {
            "error": "skill_binding invariant violation: tool_manual requires tool_binding",
            "code": "skill_binding_invariant_violation",
        }
    if tool_binding is not None and skill_class != "tool_manual":
        return {
            "error": "skill_binding invariant violation: tool_binding requires skill_class=tool_manual",
            "code": "skill_binding_invariant_violation",
        }

    if tool_binding is not None:
        exposure = tool_binding.get("exposure")
        if exposure not in _VALID_TOOL_EXPOSURES:
            return {
                "error": f"tool_binding.exposure must be primary/overflow/private, got {exposure!r}",
                "code": "skill_binding_invalid",
            }
        bound_tools = tool_binding.get("bound_tools")
        if (
            not isinstance(bound_tools, list)
            or not bound_tools
            or not all(isinstance(t, str) and t for t in bound_tools)
        ):
            return {
                "error": "tool_binding.bound_tools must be a non-empty list of non-empty strings",
                "code": "skill_binding_invalid",
            }

    if skill_class not in _KNOWN_SKILL_CLASSES:
        return {
            "warning": f"unknown skill_class {skill_class!r} — open vocabulary, accepted with advisory warning"
        }

    return None


def _op_register_skill_substrate(
    skill_id: str,
    skill_path: str,
    case_id: str | None = None,
    description: str = "",
    trigger_phrases: list[str] | None = None,
    skill_binding: dict | None = None,
    session_id: str | None = None,
    agent: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Atomic DB-only composite: agent_skill: + document: + relationship_create.

    C1 atomicity — CREATE PATH: all three writes (agent_skill entity,
    document entity, keystone_of relationship, plus the optional uses_skill
    relationship) execute on a single connection under WRITE_LOCK inside one
    explicit ``BEGIN IMMEDIATE`` … ``COMMIT``, via the conn-taking impls
    ``create_entity_impl(conn, …, commit=False)`` and
    ``create_relationship_on_conn(conn, …, commit=False)``. A mid-composite
    failure rolls back every member — partial failures cannot leave orphaned
    entities. The ``_op_*`` wrappers are bypassed on this path, which drops
    their advisory-only machinery (write-discipline nudge, collision warning,
    per-member ``mcp.cortex.*.created`` events); the RAG source-changed nudge
    is covered by the periodic backstop for ``commit=False`` callers.

    MATCHING-PATH BACKFILL is intentionally NON-atomic per member: each
    backfilled member (document entity, keystone relationship) is written by
    the wrapper ops on their own connections and is independently durable and
    idempotent — a failed backfill converges on retry.

    Idempotency (W3):
    - If agent_skill:<skill_id> exists and matches (name, source_uri canonical,
      description, trigger_phrases as set), return existing with _status="existing";
      the document member and keystone relationship are backfilled when missing
      (composite completion — the substantiation migration registers over
      pre-existing skill entities, which must end up fully substantiated).
    - If diverges, return composite_conflict with diff + suggested entity_update
      (the suggested update covers name/source_uri/description/attributes so a
      re-register after applying it converges to "existing").
    - Else create all three rows atomically.

    Canonical ``source_uri`` for new registrations:
    ``workspaces://universal-llm-gateway/.cursor/skills/{skill_id}/SKILL.md``.
    Legacy ``cortex://agent-skills/`` paths are rejected (invalid_skill_path).
    Emits cortex.composite.registered with entity_ids, composite, status.
    """
    if not skill_id or not skill_path:
        return {"error": "skill_id and skill_path are required"}

    try:
        validated_path = _validate_skill_registration_path(skill_id, skill_path)
    except ValueError as exc:
        return {"error": str(exc), "code": "invalid_skill_path"}

    if skill_binding is not None:
        _result = _validate_skill_binding(skill_binding)
        if _result and "error" in _result:
            return {**_result}
        if _result and "warning" in _result:
            logger.warning("register_skill_substrate: %s", _result["warning"])

    skill_entity_id = f"agent_skill:{skill_id}"
    document_entity_id = f"document:skill-{skill_id}"

    deferred_emits: list = []
    with WRITE_LOCK:
        conn = cortex_conn()
        try:
            # BEGIN IMMEDIATE takes the SQLite write lock up front (no
            # mid-composite busy upgrade); WRITE_LOCK above serializes
            # application-level writers around the whole atomic section.
            conn.execute("BEGIN IMMEDIATE")
            # Idempotency check (W3)
            existing = query(
                conn,
                "SELECT id, name, description, attributes, source_uri FROM entities WHERE id = ?",
                (skill_entity_id,),
            )
            if existing:
                # Read-only idempotency check complete — end the explicit tx
                # here. Everything below runs OUTSIDE a transaction; the
                # except handler must therefore never ROLLBACK unconditionally
                # (friction 22236 secondary defect: a double ROLLBACK raised
                # OperationalError "cannot rollback - no transaction is
                # active" and masked the original exception).
                conn.execute("ROLLBACK")
                skill = existing[0]
                # entities.attributes is a JSON TEXT column and query() does
                # not decode it (friction 22236 primary defect: .get() on the
                # raw string raised AttributeError for any existing row with
                # non-null attributes).
                raw_attrs = skill.get("attributes")
                attrs = (
                    raw_attrs
                    if isinstance(raw_attrs, dict)
                    else (json_decode(raw_attrs, fallback={}) or {})
                )
                current_phrases = set(attrs.get("trigger_phrases") or [])
                requested_phrases = set(trigger_phrases or [])
                current_binding = attrs.get("skill_binding")

                if (
                    skill.get("name") == skill_id
                    and str(skill.get("source_uri") or "") == str(validated_path)
                    and (skill.get("description") or "") == (description or "")
                    and current_phrases == requested_phrases
                    and current_binding == skill_binding
                ):
                    # Composite completion (W3): a matching agent_skill row
                    # may predate this composite — the substantiation
                    # migration registers over EXISTING skill entities.
                    # "existing" must still leave the full composite in
                    # place, so create the document member and keystone
                    # relationship when missing.
                    backfilled: list[str] = []
                    doc_exists = query(
                        conn,
                        "SELECT id FROM entities WHERE id = ?",
                        (document_entity_id,),
                    )
                    if not doc_exists:
                        doc_result = _op_entity_create(
                            id=document_entity_id,
                            type="document",
                            name=skill_id,
                            description=description
                            or f"Skill substrate for {skill_id}",
                            source_uri=str(validated_path),
                            attributes={
                                "lifecycle_status": "registered",
                                "kind": "agent_skill",
                            },
                            session_id=session_id,
                            agent=agent or "cursor",
                        )
                        if isinstance(doc_result, dict) and "error" in doc_result:
                            return doc_result
                        backfilled.append(document_entity_id)
                    rel_exists = query(
                        conn,
                        "SELECT id FROM relationships"
                        " WHERE from_entity = ? AND to_entity = ?"
                        " AND type = 'keystone_of' AND active = 1",
                        (skill_entity_id, document_entity_id),
                    )
                    if not rel_exists:
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
                            and "already exists"
                            not in str(rel_result.get("error", ""))
                        ):
                            return rel_result
                        backfilled.append(
                            f"{skill_entity_id} -[keystone_of]-> {document_entity_id}"
                        )
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
                        "backfilled_members": backfilled,
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
                    "skill_binding": [current_binding, skill_binding],
                }
                # Data-loss guard: never suggest replacing real data with
                # emptiness. A probe that omits description (default "") or
                # trigger_phrases must not yield a suggested update that
                # would BLANK the live values when applied via the
                # documented recovery ladder (apply suggested entity_update,
                # re-register). Observed live 2026-07-04 on thread 4266
                # against agent_skill:mcp-surface-change. Omit a field only
                # when the request value is empty AND the current value is
                # non-empty; name and source_uri are always derived
                # (skill_id / validated_path), never empty — unconditional.
                # The conflict `diff` above is intentionally untouched:
                # callers rely on it to see exactly what diverged.
                suggested_updates: dict[str, Any] = {
                    "name": skill_id,
                    "source_uri": str(validated_path),
                }
                if (description or "").strip() or not (
                    skill.get("description") or ""
                ):
                    suggested_updates["description"] = description
                suggested_attributes: dict[str, Any] = {}
                if requested_phrases or not current_phrases:
                    suggested_attributes["trigger_phrases"] = sorted(
                        requested_phrases
                    )
                if skill_binding is not None:
                    suggested_attributes["skill_binding"] = skill_binding
                if suggested_attributes:
                    suggested_updates["attributes"] = suggested_attributes
                return {
                    "error": "composite_conflict",
                    "code": "composite_conflict",
                    "message": f"agent_skill:{skill_id} exists but fields diverge",
                    "diff": diff,
                    "suggested": {
                        "op": "entity_update",
                        "entity_id": skill_entity_id,
                        # The idempotency equality also checks name and
                        # source_uri — the suggested update must cover them
                        # or a re-register after applying it can never
                        # converge to "existing". attributes merge into the
                        # prior blob server-side (update_entity_impl), so
                        # unrelated keys (e.g. applicable_agents) survive.
                        "updates": suggested_updates,
                    },
                }

            # Create — C1 atomic: both entity rows and the relationship
            # row(s) are written on THIS connection with commit=False inside
            # the BEGIN IMMEDIATE above, and become durable only at the
            # single COMMIT below. The _op_* wrappers are bypassed (they open
            # their own connections and commit independently — the pre-C1
            # orphan shape observed live on thread 4266). Same-connection
            # execution also lets create_relationship_on_conn's entity
            # existence checks see the uncommitted entity rows.
            skill_attributes: dict[str, Any] = {
                "trigger_phrases": trigger_phrases or [],
                "canonical_path": str(validated_path),
                "lifecycle_status": "registered",
            }
            if skill_binding is not None:
                skill_attributes["skill_binding"] = skill_binding
            # The bypassed wrapper computed content_hash from source_uri;
            # filesystem-only (no DB), safe pre-tx.
            content_hash = _compute_content_hash(str(validated_path))

            try:
                create_entity_impl(
                    conn,
                    {
                        "id": skill_entity_id,
                        "type": "agent_skill",
                        "name": skill_id,
                        "description": description,
                        "source_uri": str(validated_path),
                        **(
                            {"content_hash": content_hash}
                            if content_hash is not None
                            else {}
                        ),
                        "attributes": skill_attributes,
                    },
                    commit=False,
                )

                create_entity_impl(
                    conn,
                    {
                        "id": document_entity_id,
                        "type": "document",
                        "name": skill_id,
                        "description": description
                        or f"Skill substrate for {skill_id}",
                        "source_uri": str(validated_path),
                        **(
                            {"content_hash": content_hash}
                            if content_hash is not None
                            else {}
                        ),
                        "attributes": {
                            "lifecycle_status": "registered",
                            "kind": "agent_skill",
                        },
                    },
                    commit=False,
                )

                # Wire keystone relationship (per plan edge taxonomy in §3).
                # Relationship dedup (was_new=false) is success, exactly as
                # the route treats it — replaces the old error-string sniff.
                create_relationship_on_conn(
                    conn,
                    RelationshipCreate(
                        source_id=skill_entity_id,
                        target_id=document_entity_id,
                        type_id="keystone_of",
                        role="substrate",
                        evidence=f"register_skill_substrate({skill_id})",
                        session_id=session_id,
                        agent=agent or "cursor",
                    ),
                    commit=False,
                    post_commit_emits=deferred_emits,
                )

                if case_id:
                    create_relationship_on_conn(
                        conn,
                        RelationshipCreate(
                            source_id=case_id
                            if isinstance(case_id, str)
                            else str(case_id),
                            target_id=skill_entity_id,
                            type_id="uses_skill",
                            evidence=f"register_skill_substrate({skill_id})",
                            session_id=session_id,
                            agent=agent or "cursor",
                        ),
                        commit=False,
                        post_commit_emits=deferred_emits,
                    )

                conn.execute("COMMIT")
            except HTTPException as exc:
                # The conn-taking impls raise HTTPException instead of
                # returning error dicts; roll back the atomic section and
                # preserve the composite's outward error-dict contract
                # (mirrors _op_relationship_create's translation).
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                return {"error": exc.detail, "status_code": exc.status_code}
            except sqlite3.IntegrityError as exc:
                # A composite member id already exists (e.g. an orphaned
                # document: row from a pre-C1 partial failure). The bypassed
                # wrapper translated this to HTTP 409; keep that contract.
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                return {
                    "error": f"composite member already exists: {exc}",
                    "code": "composite_member_conflict",
                    "status_code": 409,
                }

        except Exception:
            # Guarded rollback (friction 22236): the existing-entity branch
            # ends the tx early, so an unconditional ROLLBACK here raised
            # OperationalError and masked the original exception.
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    # Deferred emits per the update_entity_impl post_commit_emits idiom —
    # invoked only AFTER the composite COMMIT so a rolled-back transaction
    # never leaves a false signal. (Currently empty in practice: neither
    # impl on this path emits events; the composite-level event below is
    # the only signal.)
    for emit in deferred_emits:
        try:
            emit()
        except Exception:  # noqa: BLE001 — deferred emits are advisory
            logger.warning(
                "register_skill_substrate: deferred post-commit emit failed",
                exc_info=True,
            )

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
