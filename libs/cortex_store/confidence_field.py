"""Per-type confidence-field declaration — the auditable-confidence axis.

Sibling to ``type_schemas.py`` and ``workflow_state.py``: where those
registries govern required attributes and the workflow lifecycle column,
this registry declares WHICH field carries a type's auditable confidence.
The auditor-validatability detectors (``dispatch_ops/_detectors/auditor.py``)
read it so Gate-0 membership, transcript handling, and content_hash routing
are all data consequences of one declaration — no hand-maintained scope list
(``[policy:single-source-of-truth]``, lead Q4, thread 1172).

Values:
  - ``confidence_band`` — the entity's ``confidence_band`` trait is the
                          confidence axis (normal Gate-1..4 gating). DEFAULT
                          for unregistered types after reader pass 686612ed.
  - ``status``            — legacy registry alias (pre-051); treated as
                          ``confidence_band`` at read time.
  - ``workflow_state``    — confidence rides workflow_state (e.g. todo). The
                          auditor-validatability detector does not fire.
  - ``content_hash``      — a structural verifier binds the entity to its
                          artifact (e.g. transcript). Entity-level finding
                          auto-passes iff content_hash present; fires if absent.
  - ``none``              — status is not a confidence axis (e.g. bulk test
                          fixtures). The detector does not fire.
"""

from __future__ import annotations

import sqlite3

from .db import query, table_exists

DEFAULT_CONFIDENCE_FIELD = "confidence_band"
# ``status`` retained as legacy registry alias until all DBs run migration 051.
VALID_CONFIDENCE_FIELDS = frozenset(
    {"confidence_band", "status", "workflow_state", "content_hash", "none"}
)
CONFIDENCE_BAND_AXIS_VALUES = frozenset({"confidence_band", "status"})


def uses_confidence_band_axis(field: str) -> bool:
    """True when the type's auditable confidence rides ``confidence_band``."""
    return field in CONFIDENCE_BAND_AXIS_VALUES


def confidence_field(conn: sqlite3.Connection, entity_type: str) -> str:
    """Return the declared confidence axis for *entity_type*.

    Types without a row — and pre-migration databases lacking the table —
    default to ``confidence_band``. Legacy registry rows storing ``status``
    are normalized to ``confidence_band`` at read time.
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
    raw = rows[0]["confidence_field"]
    if raw == "status":
        return "confidence_band"
    return raw


def confidence_band_sql_predicate(column_prefix: str = "") -> str:
    """Match a band on ``confidence_band`` (trait-native; one bound parameter)."""
    p = f"{column_prefix}." if column_prefix else ""
    return f"{p}confidence_band = ?"


def stored_confidence_band(row: dict[str, object]) -> str | None:
    """Stored confidence band for API/dispatch reads (``confidence_band`` trait only)."""
    band = row.get("confidence_band")
    return str(band) if band is not None else None


def lifecycle_not_value_sql_predicate(value: str, column_prefix: str = "") -> str:
    """Exclude entities at lifecycle *value* (trait-native; bind ``(value,)``)."""
    p = f"{column_prefix}." if column_prefix else ""
    return f"({p}lifecycle IS NULL OR {p}lifecycle != ?)"


def lifecycle_is_value_sql_predicate(value: str, column_prefix: str = "") -> str:
    """Match lifecycle *value* (trait-native; bind ``(value,)``)."""
    p = f"{column_prefix}." if column_prefix else ""
    return f"{p}lifecycle = ?"


# Non-production lifecycle values excluded from all agent-facing skill surfaces.
# Bind as: db_query(conn, sql, (*SUPPRESSED_SKILL_LIFECYCLES, ...other params...))
SUPPRESSED_SKILL_LIFECYCLES: tuple[str, ...] = (
    "deprecated",
    "draft",
    "retired",
    "merged",
)

# Positive filter for boot /skills — only graduated active skills.
DISCOVERABLE_SKILL_LIFECYCLE: str = "active"


def discoverable_skill_lifecycle_sql_predicate(column_prefix: str = "") -> str:
    """Match only ``lifecycle='active'`` for agent skill discovery surfaces."""
    return lifecycle_is_value_sql_predicate(DISCOVERABLE_SKILL_LIFECYCLE, column_prefix)


def agent_skill_is_discoverable(lifecycle: str | None) -> bool:
    """Whether an ``agent_skill`` row is on automatic discovery surfaces."""
    return lifecycle == DISCOVERABLE_SKILL_LIFECYCLE


def lifecycle_not_in_sql_predicate(
    values: tuple[str, ...], column_prefix: str = ""
) -> str:
    """Exclude entities at any lifecycle in *values* (bind ``values`` tuple in order).

    Generates ``(lifecycle IS NULL OR lifecycle NOT IN (?,...))``).
    NULL lifecycle is explicitly included (default-discoverable when unset).
    For single-value exclusion, ``lifecycle_not_value_sql_predicate`` (``!=``) is equivalent.
    """
    p = f"{column_prefix}." if column_prefix else ""
    placeholders = ",".join("?" for _ in values)
    return f"({p}lifecycle IS NULL OR {p}lifecycle NOT IN ({placeholders}))"


def adoption_in_sql_predicate(
    adoption_values: tuple[str, ...],
    legacy_status_values: tuple[str, ...] = ("confirmed",),
    column_prefix: str = "",
) -> str:
    """Match adoption trait (trait-native; bind adoption values only).

    *legacy_status_values* is accepted for call-site compatibility but ignored.
    """
    _ = legacy_status_values
    p = f"{column_prefix}." if column_prefix else ""
    adop_ph = ",".join(["?"] * len(adoption_values))
    return f"{p}adoption IN ({adop_ph})"
