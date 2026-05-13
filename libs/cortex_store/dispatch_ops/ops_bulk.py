"""Public façade for bulk Cortex dispatch ops.

Re-exports the entity and relationship bulk upsert handlers from their
implementation siblings so callers (the dispatch registry, tests, and any
future intra-package users) have a single import path. The split into
``ops_bulk_entities.py`` and ``ops_bulk_relationships.py`` exists to keep
each implementation file under the new-file SLOC cap.
"""

from __future__ import annotations

from .ops_bulk_entities import _op_entities_bulk_upsert
from .ops_bulk_relationships import _op_relationships_bulk_upsert

__all__ = ["_op_entities_bulk_upsert", "_op_relationships_bulk_upsert"]
