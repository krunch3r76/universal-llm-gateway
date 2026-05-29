"""``sqlite_query`` source runner for ``data_source_v1``.

Executes a single read-only SQL query against an allowlisted SQLite database and
returns its rows as a JSON-serializable payload. Enforces three guards inherited
from the monolith: the statement must be a ``SELECT``/``WITH`` read, the
``db_path`` must resolve inside the fixed allowlist, and the result set is
clamped to ``max_rows`` (default 5000, bounded to [1, 50000]) with a
``truncated`` flag. Optional ``params`` are bound via the handler-input resolver.
"""

from __future__ import annotations

import contextlib
import sqlite3
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ...execution.resolver import NamespaceResolver
from .allowlist import expand_allowed_db
from .bindings import resolve_binding

if TYPE_CHECKING:
    from ...schemas import StepConfig
    from ..protocol import PipelineContext

logger = get_logger(__name__)


async def run_sqlite(step: StepConfig, context: PipelineContext) -> dict[str, Any]:
    """Executes a read-only SQLite query and returns the results.

    Args:
        step: The `StepConfig` containing 'db_path', 'sql', and optional 'params'.
        context: The `PipelineContext` for resolving bindings.

    Returns:
        A dictionary containing 'columns', 'rows', 'truncated', and 'row_count'.

    Raises:
        ValueError: If `db_path` or `sql` are missing, `sql` is not read-only,
                    `db_path` is disallowed, or parameter binding fails.
        sqlite3.Error: If a SQLite database error occurs during execution.
    """
    db_path_s = step.get_domain_field("db_path", "")
    sql = step.get_domain_field("sql", "")
    if not db_path_s or not sql:
        raise ValueError(
            f"Step '{step.id}': sqlite_query requires db_path and sql in step config"
        )
    sql_u = sql.strip().upper()
    if not sql_u.startswith("SELECT") and not sql_u.startswith("WITH"):
        raise ValueError(
            f"Step '{step.id}': sqlite_query allows read-only SELECT/WITH only"
        )
    resolved = expand_allowed_db(db_path_s)
    if resolved is None:
        raise ValueError(f"Step '{step.id}': disallowed or invalid db_path")
    params: tuple[Any, ...] = ()
    resolver = NamespaceResolver(context)
    if step.handler_inputs.get("params"):
        params = resolve_binding(resolver, step, "params")
        if not isinstance(params, list | tuple):
            params = (params,)
        params = tuple(params)

    max_rows = int(step.get_domain_field("max_rows", 5000) or 5000)
    max_rows = min(max(max_rows, 1), 50_000)

    try:
        with contextlib.closing(
            sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
        ) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(sql, params)
            rows = cur.fetchmany(max_rows + 1)
    except sqlite3.Error as e:
        logger.error("[%s] sqlite_query failed: %s", step.id, e, exc_info=True)
        raise ValueError(f"SQLite query failed for step '{step.id}': {e}") from e

    truncated = len(rows) > max_rows
    if truncated:
        rows = rows[:max_rows]
    columns = list(rows[0].keys()) if rows else []
    out_rows = [dict(r) for r in rows]
    return {
        "columns": columns,
        "rows": out_rows,
        "truncated": truncated,
        "row_count": len(out_rows),
    }
