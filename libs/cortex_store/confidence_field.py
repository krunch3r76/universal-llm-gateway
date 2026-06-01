"""Per-type confidence-field declaration — the auditable-confidence axis.

Sibling to ``type_schemas.py`` and ``workflow_state.py``: where those
registries govern required attributes and the workflow lifecycle column,
this registry declares WHICH field carries a type's auditable confidence.
The auditor-validatability detectors (``dispatch_ops/_detectors/auditor.py``)
read it so Gate-0 membership, transcript handling, and content_hash routing
are all data consequences of one declaration — no hand-maintained scope list
(``[policy:single-source-of-truth]``, lead Q4, thread 1172).

Values:
  - ``status``         — the entity's status column is the confidence axis
                          (normal Gate-1..4 gating). DEFAULT for unregistered
                          types, preserving historical detector behavior.
  - ``workflow_state`` — confidence rides workflow_state (e.g. todo). The
                          auditor-validatability detector does not fire.
  - ``content_hash``   — a structural verifier binds the entity to its
                          artifact (e.g. transcript). Entity-level finding
                          auto-passes iff content_hash present; fires if absent.
  - ``none``           — status is not a confidence axis (e.g. bulk test
                          fixtures). The detector does not fire.
"""

from __future__ import annotations

import sqlite3

from .db import query, table_exists

DEFAULT_CONFIDENCE_FIELD = "status"
VALID_CONFIDENCE_FIELDS = frozenset(
    {"status", "workflow_state", "content_hash", "none"}
)


def confidence_field(conn: sqlite3.Connection, entity_type: str) -> str:
    """Return the declared confidence axis for *entity_type*.

    Types without a row — and pre-migration databases lacking the table —
    default to ``status`` (the historical detector behavior). The change is
    therefore purely additive: only explicitly-declared non-``status`` types
    change behavior.
    """
    if not table_exists(conn, "type_confidence_fields"):
        return DEFAULT_CONFIDENCE_FIELD
    rows = query(
        conn,
        "SELECT confidence_field FROM type_confidence_fields WHERE entity_type = ?",
        (entity_type,),
    )
    if not rows:
        return DEFAULT_CONFIDENCE_FIELD
    return rows[0]["confidence_field"]
