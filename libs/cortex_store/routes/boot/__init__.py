"""GET /boot-sections — salience-driven entity sections for boot briefings."""

from fastapi import APIRouter

from . import (
    audit_counters,
    commitments,
    continuity,
    control_tower,
    legal_contacts,
    principal_context,
    recent_mentions,
    recent_work,
    reflective,
    sections,
    temporal,
    todos,
)

router = APIRouter(tags=["boot"])
router.include_router(sections.router)
router.include_router(temporal.router)
router.include_router(todos.router)
router.include_router(commitments.router)
router.include_router(continuity.router)
router.include_router(control_tower.router)
router.include_router(legal_contacts.router)
router.include_router(principal_context.router)
router.include_router(recent_mentions.router)
router.include_router(recent_work.router)
router.include_router(reflective.router)
router.include_router(audit_counters.router)

__all__ = ["router"]
