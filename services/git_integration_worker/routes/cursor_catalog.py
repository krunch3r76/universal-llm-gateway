"""Cursor SDK catalog HTTP route for Stargate catalog polling."""

from __future__ import annotations

from typing import Any

from cursor_capabilities import (
    CURSOR_MODEL_CAPABILITIES,
    DESCRIPTOR_VERSION,
    to_model_card_dict,
)
from fastapi import APIRouter
from universal_logging import get_logger

from services.git_integration_worker.cursor_models import project_live_catalog

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/cursor", tags=["cursor-catalog"])


def _build_catalog_entries(models: list[Any]) -> list[dict[str, Any]]:
    projected = project_live_catalog(models)
    entries: list[dict[str, Any]] = []
    for model in models:
        bare_id = model.id
        projected_entry = projected.get(bare_id, {})
        capability = CURSOR_MODEL_CAPABILITIES.get(bare_id)
        dispatch = (
            to_model_card_dict(capability)
            if capability is not None
            else {"knobs": {}, "fixed_params": {}}
        )
        entries.append(
            {
                "id": bare_id,
                "cursor_id": f"cursor/{bare_id}",
                "knobs": projected_entry.get("knobs", {}),
                "default_variant": projected_entry.get("default_variant", {}),
                "dispatch": dispatch,
            }
        )
    return entries


@router.get("/catalog", summary="Live Cursor SDK model catalog projection.")
async def cursor_catalog() -> dict[str, Any]:
    """Project ``Client.list_models()`` for Stargate catalog polling."""
    from cursor_sdk import Client

    models = Client().list_models()
    entries = _build_catalog_entries(models)
    logger.debug("cursor catalog: %d models", len(entries))
    return {
        "descriptor_version": DESCRIPTOR_VERSION,
        "model_count": len(entries),
        "models": entries,
    }
