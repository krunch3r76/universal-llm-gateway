"""Shared rich-seed-floor todo creation for recon front-doors (friction, objective).

Both friction_close (Phase 3) and session_close (Phase 4) promote work into the
recon front-half at the rich-seed floor (decision:todo-creation-rich-seed-contract).
density_triage defaults to ``recon_pending`` per verdict A
(decision:recon-locus-attribute-not-state); callers may override.
"""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

logger = get_logger("cortex-api.dispatch_ops.recon_seed")


def seed_recon_todo(
    *,
    todo_id: str,
    name: str,
    source_uri: str,
    required_skills: list[str],
    seed_ack: str,
    context_target_id: str,
    extra_attrs: dict[str, Any] | None = None,
    density_triage: str = "recon_pending",
    context_type_id: str = "references",
    agent: str = "unknown",
    session_id: str = "recon-seed",
) -> dict[str, Any] | None:
    """Create ``todo_id`` at the rich-seed floor if missing; edge it to context.

    Returns ``{"todo_created": id}`` on create, ``None`` when it already exists,
    or ``{"error": ...}`` on create failure. The provenance edge is best-effort
    (a missing context target must not fail the promotion). ``seed_ack`` documents
    the auto-promotion so ``detect_todo_implementation_seed_incomplete`` does not
    flag the stub before recon authors the dense spec.
    """
    from .ops_entities import _op_entity_create, _op_entity_get
    from .ops_relationships import _op_relationship_create

    existing = _op_entity_get(entity_id=todo_id)
    if isinstance(existing, dict) and "error" not in existing:
        return None

    attributes: dict[str, Any] = {
        "seed_contract_ack": seed_ack,
        "density_triage": density_triage,
    }
    # required_skills is an implement-lane key: an empty list is a schema reject
    # (type_schemas._implement_lane_shape_invalid). recon-pending stubs predate
    # densification, so omit the key entirely when no skills are known yet — it
    # is distilled at Gate-2 close, not at the rich-seed floor.
    if required_skills:
        attributes["required_skills"] = required_skills
    if extra_attrs:
        attributes.update(extra_attrs)

    created = _op_entity_create(
        id=todo_id,
        type="todo",
        name=name[:120],
        workflow_state="open",
        source_uri=source_uri,
        attributes=attributes,
    )
    if isinstance(created, dict) and "error" in created:
        # Concurrent-create dedup: a racing close may have created the todo
        # between our preflight-get and this create. Treat post-conflict
        # existence as the dedup path (None), not a hard failure.
        post = _op_entity_get(entity_id=todo_id)
        if isinstance(post, dict) and "error" not in post:
            return None
        logger.warning(
            "seed_recon_todo create failed for %s: %s", todo_id, created["error"]
        )
        return created

    try:
        _op_relationship_create(
            source_id=todo_id,
            target_id=context_target_id,
            type_id=context_type_id,
            agent=agent,
            session_id=session_id,
        )
    except Exception as exc:  # noqa: BLE001 — provenance edge is best-effort
        logger.warning(
            "seed_recon_todo edge %s -> %s failed: %s", todo_id, context_target_id, exc
        )

    return {"todo_created": todo_id}


__all__ = ["seed_recon_todo"]
