"""Bounded signal extraction from a canonical build envelope.

The dict returned here is written to the spool AND returned inline by
fetch_result(format="signals") — same computation, one source of truth
(decision #5). Every field is bounded so the result never exceeds the MCP
response-size guard threshold.
"""

from __future__ import annotations

import re
from typing import Any

# Lines matching these markers are surfaced as failure signals. Anchored to
# common pytest / runtime failure vocabulary; case-sensitive on the all-caps
# tokens to avoid matching prose like "no errors".
_FAILURE_RE = re.compile(
    r"\b(FAILED|ERROR|ERRORS|AssertionError|Traceback|FAIL)\b"
)
_DEFAULT_TAIL_LINES = 20
_MAX_FAILURE_LINES = 50
_MAX_LINE_CHARS = 500


def _clip(line: str) -> str:
    """Clip a single line so one pathological line cannot blow the budget."""
    return line if len(line) <= _MAX_LINE_CHARS else line[:_MAX_LINE_CHARS] + "…"


def compute_signals(
    envelope: dict[str, Any], *, tail_lines: int = _DEFAULT_TAIL_LINES
) -> dict[str, Any]:
    """Extract the bounded signal dict from a canonical build envelope.

    Reads only top-level envelope fields and ``metadata`` — never touches the
    sidecar — so it is cheap and backend-neutral.
    """
    meta = envelope.get("metadata", {}) or {}
    stdout = str(envelope.get("stdout", "") or "")
    stderr = str(envelope.get("stderr", "") or "")
    stdout_lines = stdout.splitlines()
    stderr_lines = stderr.splitlines()
    failures = [_clip(ln) for ln in stdout_lines if _FAILURE_RE.search(ln)]
    return {
        "dispatch_id": envelope.get("dispatch_id", ""),
        "status": envelope.get("status", ""),
        "exit_code": envelope.get("exit_code"),
        "duration_s": envelope.get("duration_s"),
        "reason_code": meta.get("reason_code", ""),
        "read_only_violation": bool(meta.get("read_only_violation", False)),
        "audit_incomplete": bool(meta.get("audit_incomplete", False)),
        "git_diff_stat": str(meta.get("git_diff_stat", "") or ""),
        "stdout_tail": [_clip(ln) for ln in stdout_lines[-tail_lines:]],
        "stderr_tail": [_clip(ln) for ln in stderr_lines[-tail_lines:]],
        "failure_lines": failures[:_MAX_FAILURE_LINES],
        "failure_count": len(failures),
    }
