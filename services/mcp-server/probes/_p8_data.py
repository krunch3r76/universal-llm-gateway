"""Data constants for P8 probe — SQL queries, D9 bug pointer, gate-criterion notes.

Kept here so the probe's main module stays under SLOC budget per [quality].
Full D9 bug diagnosis lives at:
  cortex://notes/system/post-mortems/d9-cache-invariance-probe-bug.md
"""

from __future__ import annotations

_BASELINE_SQL = """
SELECT
  json_extract(payload, '$.mcp_method') AS mcp_method,
  COUNT(*) AS count,
  AVG(CAST(json_extract(payload, '$.response_bytes') AS REAL)) AS avg_response_bytes,
  MAX(CAST(json_extract(payload, '$.response_bytes') AS REAL)) AS max_response_bytes
FROM events
WHERE signal = 'mcp.request.completed'
  AND json_extract(payload, '$.seat_class') = 'claude'
GROUP BY mcp_method
ORDER BY count DESC
"""

_TOOLS_LIST_SQL = """
SELECT
  json_extract(payload, '$.response_bytes') AS response_bytes,
  json_extract(payload, '$.duration_s') AS duration_s,
  json_extract(payload, '$.auth_mode') AS auth_mode,
  timestamp
FROM events
WHERE signal = 'mcp.request.completed'
  AND json_extract(payload, '$.mcp_method') = 'tools/list'
  AND json_extract(payload, '$.seat_class') = 'claude'
ORDER BY ts_unix_ms DESC
LIMIT 20
"""

_D9_BUG_SUMMARY = {
    "probe": "d9-cache-invariance-probe.sh",
    "symptom": "baseline returned empty events",
    "summary": (
        "(1) Wrong socket query protocol — sent unrecognized {op,params} dict, "
        "received silent empty result. (2) mcp.request.completed payloads "
        "lack cache_read_input_tokens; Anthropic API cache metrics are not "
        "propagated to the gateway event service."
    ),
    "fix": (
        "Use scripts/query-events --sql with json_extract; response_bytes "
        "for tools/list (seat_class=claude) as cache-stability proxy."
    ),
    "details": "cortex://notes/system/post-mortems/d9-cache-invariance-probe-bug.md",
    "todo_entity": "todo:d9-probe-fix",
}

_POST_D0_CHECK_NOTE = (
    "After D0 container rebuild, query mcp.request.completed "
    "WHERE mcp_method='tools/list' AND seat_class='claude'. "
    "New response_bytes should be computed from 11-domain manifest. "
    "If response_bytes stabilizes within 2 restarts, cache is stable."
)

_ANTHROPIC_CACHE_NOTE = (
    "NEEDS-HUMAN-CALL: cache_read_input_tokens not in event payloads. "
    "Monitor claude-ai traffic via Anthropic dashboard or instrument "
    "gateway to log cache headers from API responses."
)


__all__ = [
    "_ANTHROPIC_CACHE_NOTE",
    "_BASELINE_SQL",
    "_D9_BUG_SUMMARY",
    "_POST_D0_CHECK_NOTE",
    "_TOOLS_LIST_SQL",
]
