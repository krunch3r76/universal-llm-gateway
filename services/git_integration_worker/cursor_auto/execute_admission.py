"""Admission for ``contract: execute`` — bounded tier-M tool ops, no repo writes.

Fable CDP↔cursor-auto lane consult §1 Option A. ``execute`` is admitted against
the tier-M manifest rather than the repo-shaped scope regexes: the body must name
exactly one ``tool_op:`` that the manifest allows unattended, declare the
``effects_expected:`` the closeout will carry, and its arguments must parse.
Everything else is refused *before* anything runs, so a denied op can never
produce an executed-looking closeout (invariant 1: never claim executed without
an observed tool payload).

Single-op only in v1. A multi-op ask carries sequencing judgment, which belongs
to a nested contract, not to an in-seat runner.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from services.git_integration_worker.cursor_auto.fix_hints import (
    EXECUTE_EFFECTS_MISSING_FIX_HINT,
    EXECUTE_NOT_ALLOWLISTED_FIX_HINT,
    EXECUTE_TOOL_OP_FIX_HINT,
)
from services.git_integration_worker.cursor_auto.tier_m_manifest import (
    MANIFEST_BIND_URI,
    ManifestRow,
    allowed_tool_ops,
    lookup,
)

EXECUTE_CONTRACT = "execute"

_TOOL_OP_RE = re.compile(r"(?im)^[ \t]*tool_op:[ \t]*(\S+)")
_TOOL_ARGS_RE = re.compile(r"(?im)^[ \t]*tool_args:[ \t]*(.+)$")
_EFFECTS_EXPECTED_RE = re.compile(r"(?im)^[ \t]*effects_expected:[ \t]*(\S+)")


@dataclass(frozen=True, slots=True)
class ExecuteAdmission:
    """Verdict for one ``execute`` body: the approved row, or a blocking error."""

    tokens: tuple[str, ...]
    row: ManifestRow | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None

    @property
    def approved(self) -> bool:
        """True when exactly one manifest-allowed op with parsed args was found."""
        return self.error is None and self.row is not None


def _error(
    reason: str,
    summary: str,
    fix_hint: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "reason": reason,
        "summary": summary,
        "fix_hint": fix_hint,
        **extra,
    }


def parse_tool_op_tokens(body: str) -> tuple[str, ...]:
    """Return every ``tool_op:`` token declared in *body*, in authored order."""
    return tuple(
        m.group(1).strip().strip("`") for m in _TOOL_OP_RE.finditer(body or "")
    )


def has_effects_expected(body: str) -> bool:
    """True when the body declares the observable result the closeout must carry."""
    return _EFFECTS_EXPECTED_RE.search(body or "") is not None


def parse_tool_args(body: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse the single-line ``tool_args:`` JSON object.

    Returns ``(arguments, raw_on_failure)``. An absent line is an empty argument
    map — some ops take none. A present but unparseable line is a failure, never
    a silent empty map.
    """
    match = _TOOL_ARGS_RE.search(body or "")
    if match is None:
        return {}, None
    raw = match.group(1).strip().strip("`")
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None, raw
    if not isinstance(parsed, dict):
        return None, raw
    return parsed, None


def _resolve_row(token: str) -> tuple[ManifestRow | None, dict[str, Any] | None]:
    """Look *token* up in the manifest; a dict means the op may not run."""
    row = lookup(token)
    if row is None:
        return None, _error(
            "execute_tool_op_not_in_manifest",
            f"tool_op {token!r} has no tier-M manifest row — deny-by-default.",
            EXECUTE_NOT_ALLOWLISTED_FIX_HINT,
            tool_op=token,
            manifest_bind_uri=MANIFEST_BIND_URI,
            allowed_tool_ops=list(allowed_tool_ops()),
        )
    if not row.allowed:
        return row, _error(
            "execute_tool_op_denied",
            f"tier-M manifest denies {row.tool_op} unattended: {row.note}.",
            EXECUTE_NOT_ALLOWLISTED_FIX_HINT,
            tool_op=row.tool_op,
            manifest_note=row.note,
            manifest_bind_uri=MANIFEST_BIND_URI,
            allowed_tool_ops=list(allowed_tool_ops()),
        )
    return row, None


def admit_execute_body(body: str) -> ExecuteAdmission:
    """Resolve an ``execute`` DIRECTIVE body against the tier-M manifest."""
    tokens = parse_tool_op_tokens(body)
    if not tokens:
        return ExecuteAdmission(
            tokens=tokens,
            error=_error(
                "execute_tool_op_missing",
                "contract=execute requires a tool_op: line naming the op to fire.",
                EXECUTE_TOOL_OP_FIX_HINT,
                allowed_tool_ops=list(allowed_tool_ops()),
            ),
        )
    if len(tokens) > 1:
        return ExecuteAdmission(
            tokens=tokens,
            error=_error(
                "execute_multi_op_unsupported",
                (
                    f"contract=execute admits one op; {len(tokens)} declared "
                    "— sequencing is judgment, route it as contract=implement."
                ),
                EXECUTE_TOOL_OP_FIX_HINT,
                declared_tool_ops=list(tokens),
            ),
        )
    row, denial = _resolve_row(tokens[0])
    if denial is not None:
        return ExecuteAdmission(tokens=tokens, error=denial)
    if not has_effects_expected(body):
        return ExecuteAdmission(
            tokens=tokens,
            error=_error(
                "execute_effects_expected_missing",
                (
                    "contract=execute requires effects_expected: naming the "
                    "observable result the closeout must carry."
                ),
                EXECUTE_EFFECTS_MISSING_FIX_HINT,
                tool_op=tokens[0],
            ),
        )
    arguments, bad_raw = parse_tool_args(body)
    if arguments is None:
        return ExecuteAdmission(
            tokens=tokens,
            row=row,
            error=_error(
                "execute_tool_args_unparseable",
                "tool_args: must be a single-line JSON object.",
                EXECUTE_TOOL_OP_FIX_HINT,
                tool_op=tokens[0],
                provided=bad_raw,
            ),
        )
    return ExecuteAdmission(tokens=tokens, row=row, arguments=arguments)


__all__ = [
    "EXECUTE_CONTRACT",
    "ExecuteAdmission",
    "admit_execute_body",
    "has_effects_expected",
    "parse_tool_args",
    "parse_tool_op_tokens",
]
