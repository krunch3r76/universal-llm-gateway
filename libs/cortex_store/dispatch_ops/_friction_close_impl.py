"""friction_close implementation — supersede friction with a resolution assertion."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

from ..routes.assertions import _supersede_assertion_impl
from ._shared import record
from .ops_assertions_update import _op_assertion_get

logger = get_logger("cortex-api.dispatch_ops.assertions")

_RESOLUTION_KIND_EXACT = frozenset({"superseded", "wontfix"})
_RESOLUTION_KIND_PREFIXES = ("agent_skill:", "workflow:", "todo:")


def validate_resolution_kind(resolution_kind: str) -> str | None:
    """Return an error message when *resolution_kind* is invalid, else None."""
    if resolution_kind in _RESOLUTION_KIND_EXACT:
        return None
    for prefix in _RESOLUTION_KIND_PREFIXES:
        if resolution_kind.startswith(prefix) and len(resolution_kind) > len(prefix):
            return None
    return (
        f"Invalid resolution_kind={resolution_kind!r}. Must be one of: "
        "agent_skill:{slug}, workflow:{slug}, todo:{slug}, superseded, wontfix"
    )


def _promote_friction_to_todo(
    *,
    resolution_kind: str,
    friction_entity_id: str,
    friction_assertion_id: int,
    friction_claim: str,
    agent: str,
    session_id: str,
) -> dict[str, Any] | None:
    """Promote a closed friction into a recon-pending todo when it does not exist."""
    from ._recon_seed import seed_recon_todo

    slug = resolution_kind.removeprefix("todo:")
    required_skills: list[str] = []
    if friction_entity_id.startswith("agent_skill:"):
        required_skills = [friction_entity_id.removeprefix("agent_skill:")]

    result = seed_recon_todo(
        todo_id=resolution_kind,
        name=friction_claim or f"Promoted from friction #{friction_assertion_id}",
        source_uri=f"tasks/specs/{slug}.md",
        required_skills=required_skills,
        seed_ack=(
            f"auto-promoted from friction #{friction_assertion_id}; "
            "recon pending (density_triage=recon_pending)"
        ),
        context_target_id=friction_entity_id,
        extra_attrs={"promoted_from_friction": friction_assertion_id},
        agent=agent,
        session_id=session_id,
    )
    if result and "todo_created" in result:
        record(
            "cortex.friction.todo.promoted",
            assertion_id=friction_assertion_id,
            todo_id=resolution_kind,
            friction_entity_id=friction_entity_id,
            agent=agent,
        )
    return result


def close_friction_assertion(
    assertion_id: int,
    resolution_kind: str,
    *,
    agent: str = "unknown",
    session_id: str = "friction-close",
    evidence: str | None = None,
    resolution_note: str | None = None,
) -> dict[str, Any]:
    kind_err = validate_resolution_kind(resolution_kind)
    if kind_err:
        return {"error": kind_err}

    existing = _op_assertion_get(assertion_id=assertion_id)
    if "error" in existing:
        return existing

    superseded_by = existing.get("superseded_by")
    if superseded_by is not None:
        return {
            "status": "already_closed",
            "assertion_id": assertion_id,
            "fulfillment_assertion_id": superseded_by,
            "resolution_kind": resolution_kind,
        }

    entity_id = existing.get("entity_id")
    if not entity_id:
        return {"error": f"Friction assertion {assertion_id} has no entity_id"}

    # Promote BEFORE superseding the friction. Promotion can fail (e.g. schema
    # reject) and superseding is irreversible-by-early-return: a closed friction
    # short-circuits at the superseded_by guard above, so a post-supersede
    # promotion failure permanently drops the todo: intent with no recovery
    # path. Running promotion first lets a failure return an error while the
    # friction is still open and the close is replayable.
    promotion: dict[str, Any] | None = None
    if resolution_kind.startswith("todo:"):
        promotion = _promote_friction_to_todo(
            resolution_kind=resolution_kind,
            friction_entity_id=entity_id,
            friction_assertion_id=assertion_id,
            friction_claim=str(existing.get("claim") or ""),
            agent=agent,
            session_id=session_id,
        )
        if isinstance(promotion, dict) and "error" in promotion:
            return {
                "error": f"friction_close promotion failed: {promotion['error']}",
                "assertion_id": assertion_id,
                "resolution_kind": resolution_kind,
            }

    claim = f"[resolved:{resolution_kind}] Friction #{assertion_id} closed."
    if resolution_note:
        claim = f"{claim} {resolution_note.strip()}"

    resolved_evidence = evidence or (
        f"friction_close(assertion_id={assertion_id}, "
        f"resolution_kind={resolution_kind}) at "
        f"{datetime.now(UTC).isoformat()}"
    )

    supersede_body: dict[str, Any] = {
        "old_assertion_id": assertion_id,
        "entity_id": entity_id,
        "claim": claim,
        "confidence": "confirmed",
        "evidence": resolved_evidence,
        "derivation_type": "agent_observation",
        "confidence_score": 1.0,
        "session_id": session_id,
        "agent": agent,
        "seeded_by": agent,
    }

    try:
        result = _supersede_assertion_impl(supersede_body)
    except Exception as exc:  # HTTPException from route layer
        detail = getattr(exc, "detail", str(exc))
        return {"error": f"friction_close supersede failed: {detail}"}

    if "error" in result:
        return result

    new_item = result.get("new") or result.get("item") or {}
    fulfillment_id = new_item.get("id") if isinstance(new_item, dict) else None

    logger.info(
        "cortex friction_close: %d -> %s via %s",
        assertion_id,
        fulfillment_id,
        resolution_kind,
    )
    record(
        "mcp.cortex.friction.closed",
        assertion_id=assertion_id,
        fulfillment_assertion_id=fulfillment_id,
        resolution_kind=resolution_kind,
        agent=agent,
    )

    return {
        "status": "closed",
        "assertion_id": assertion_id,
        "fulfillment_assertion_id": fulfillment_id,
        "resolution_kind": resolution_kind,
        "item": new_item,
        **({"promotion": promotion} if promotion else {}),
    }
