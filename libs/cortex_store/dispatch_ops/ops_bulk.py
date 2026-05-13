"""Compatibility re-exports for bulk Cortex dispatch ops."""

from __future__ import annotations

from .ops_bulk_entities import _op_entities_bulk_upsert
from .ops_bulk_relationships import _op_relationships_bulk_upsert

__all__ = ["_op_entities_bulk_upsert", "_op_relationships_bulk_upsert"]
