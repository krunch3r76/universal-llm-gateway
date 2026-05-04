"""Import-time guard that a SQL projection constant covers the required field
set of its pydantic response model.

Motivated by agent-bus thread 882 turn 13: `_ASSERTION_COMPACT_COLS` once
omitted `created_at`, which `AssertionItem` requires non-optional. The
mismatch was silent — every row hit the route's try/except and the client
saw zero items, not a validation error. This module turns that class of
mismatch into a startup-time `AssertionError` naming the offending constant
by file:line so the next occurrence fires at `sync_restart cortex_api`,
not at first request.

See `agent-skills/architecture-invariants.md` §`Projection fidelity` for the
broader invariant (renderer-side derivations from payload-field content are
an architectural smell; the projection is the contract).
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel


def required_field_names(model: type[BaseModel]) -> set[str]:
    """Field names declared required (no default, not Optional) on ``model``."""
    return {name for name, field in model.model_fields.items() if field.is_required()}


def _locate_constant(source_file: str, const_name: str) -> int | None:
    """Return 1-indexed line of ``const_name``'s definition in ``source_file``.

    Self-updating: if the constant moves within its module, the next import
    picks up the new line without any edit to the caller.
    """
    try:
        source = Path(source_file).read_text()
    except OSError:
        return None
    pattern = re.compile(rf"^\s*{re.escape(const_name)}\s*=", re.MULTILINE)
    for i, line in enumerate(source.splitlines(), start=1):
        if pattern.match(line):
            return i
    return None


def assert_projection_covers_required(
    *,
    cols: str,
    model: type[BaseModel],
    const_name: str,
    source_file: str,
) -> None:
    """Assert that comma-separated ``cols`` ⊇ required fields of ``model``.

    Raises ``AssertionError`` at import time when the projection is
    underspecified. The message names the offending constant as
    ``path/to/module.py:LINE`` so the failure points at the constant, not at
    the import path — making the fix mechanical even six months out.

    Parameters
    ----------
    cols:
        The projection constant's value (comma-separated SQL column names,
        optionally with ``a.`` prefixes which are stripped for comparison).
    model:
        The pydantic response model whose required fields must be covered.
    const_name:
        The projection constant's Python name (used for source location and
        the error message).
    source_file:
        ``__file__`` of the module that defines ``const_name``.
    """
    projected = {c.strip().split(".")[-1] for c in cols.split(",") if c.strip()}
    required = required_field_names(model)
    missing = required - projected
    if not missing:
        return

    line = _locate_constant(source_file, const_name)
    location = f"{source_file}:{line}" if line is not None else source_file
    raise AssertionError(
        f"Projection `{const_name}` at {location} is underspecified for "
        f"{model.__name__}: missing required fields {sorted(missing)!r}. "
        f"Every required field of the response model must appear in the "
        f"SQL projection, or the route will fail deserialization silently. "
        f"See agent-skills/architecture-invariants.md § Projection fidelity."
    )
