"""Self-repair hints for blocked admit payloads (Fable §5 fix-hint primitive).

A blocked payload that only names what was missing is a dead end for a
codeblind operator seat. Each hint names the exact lines to add, so the
authoring seat can re-issue without a round trip.
"""

from __future__ import annotations

TIER_M_TEMPLATE_REF = "cdp-operator-proxy §2 (tier-M DIRECTIVE template)"

EMPTY_SCOPE_FIX_HINT = (
    "Add an actionable scope line and re-issue: `scope: <files or tool-op>` "
    "(repo work) OR `tool_op: <tool>.<op>` + `effects_expected: <observable "
    "result>` (tier-M tool ask), plus `files_expected:` and `vision:`. "
    f"Template: {TIER_M_TEMPLATE_REF}."
)

VISION_MISSING_FIX_HINT = (
    "Add a `vision:` line stating why this work matters (one line; "
    "`vision: mechanical — <relay/fix description>` is sufficient for "
    f"tier-M tool ops) and re-issue. Template: {TIER_M_TEMPLATE_REF}."
)

EXECUTE_TOOL_OP_FIX_HINT = (
    "`contract: execute` runs exactly one manifest-approved tier-M op. Add "
    "`tool_op: <tool>.<op>` (one line, one op) plus `tool_args: {\"k\": \"v\"}` "
    "as a single-line JSON object, and `effects_expected: <observable result>`. "
    f"Template: {TIER_M_TEMPLATE_REF}."
)

EXECUTE_EFFECTS_MISSING_FIX_HINT = (
    "`contract: execute` requires `effects_expected:` naming the observable result "
    "(what the closeout must carry verbatim). Add that line and re-issue."
)

EXECUTE_NOT_ALLOWLISTED_FIX_HINT = (
    "That op is not allowed unattended by the tier-M manifest. Re-issue with an "
    "allowlisted op (the blocked payload lists them), or route the ask as "
    "`contract: implement` with a DIRECTIVE so a nested executor runs it under "
    "the normal gates. Widening the manifest requires an operator bind."
)

PROPAGATE_SCOPE_FIX_HINT = (
    "`contract: propagate` schedules a drain-gated service restart. Add "
    "`effects_expected:` plus either a `## propagation` fenced YAML block "
    "(`propagation: [{service, code_ref, proof_class}]`) or "
    "`scope: propagation sync_restart <service>` with optional `code_ref:`."
)

PROPAGATE_MISSING_FIX_HINT = (
    "Propagation rows are missing. Add a `## propagation` YAML block listing "
    "at least one service + code_ref + proof_class, or shorthand "
    "`scope: propagation sync_restart mcp` with `effects_expected:`."
)

DEADLINE_UNPARSEABLE_FIX_HINT = (
    "`deadline:` accepts a relative window (`+15m`, `+2h`, `+90s`) or an "
    "ISO-8601 timestamp (`2026-07-29T18:00:00Z`). Fix the value and re-issue."
)

__all__ = [
    "DEADLINE_UNPARSEABLE_FIX_HINT",
    "EMPTY_SCOPE_FIX_HINT",
    "PROPAGATE_MISSING_FIX_HINT",
    "PROPAGATE_SCOPE_FIX_HINT",
    "EXECUTE_EFFECTS_MISSING_FIX_HINT",
    "EXECUTE_NOT_ALLOWLISTED_FIX_HINT",
    "EXECUTE_TOOL_OP_FIX_HINT",
    "TIER_M_TEMPLATE_REF",
    "VISION_MISSING_FIX_HINT",
]
