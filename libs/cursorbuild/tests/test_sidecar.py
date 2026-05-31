"""Tests for cursorbuild.sidecar (Phase 2 probes + kernel).

Uses the B3 stream-json sample shapes from the dispatch packet verbatim.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from cursorbuild.sidecar import (
    SIDECAR_STDOUT_LINE_MAX,
    _capture_post_state,
    _try_append_sidecar,
    _try_append_sidecar_chunk,
    extract_usage,
    parse_tool_calls,
    snap_session_id,
)

# B3 sample shapes from the packet (verbatim, including the rejected variant)
B3_INIT = b'{"type":"system","subtype":"init","apiKeySource":"login","cwd":"/tmp","session_id":"fcc7566c-1234-5678-90ab-cdef12345678","model":"Composer 2.5 Fast","permissionMode":"default"}'
B3_TOOL_START = b'{"type":"tool_call","subtype":"started","call_id":"tool_90bb1111-2222-3333-4444-555566667777","tool_call":{"mcpToolCall":{"args":{"name":"vortex-tool_search","args":{"query":"cortex"},"toolName":"tool_search","providerIdentifier":"vortex"}}},"session_id":"fcc7566c-1234-5678-90ab-cdef12345678"}'
B3_TOOL_COMPLETED = b'{"type":"tool_call","subtype":"completed","call_id":"tool_90bb1111-2222-3333-4444-555566667777","tool_call":{"mcpToolCall":{"args":{"name":"vortex-tool_search","args":{"query":"cortex"},"toolName":"tool_search","providerIdentifier":"vortex"},"result":{"success":{"content":[{"text":{"text":"ok"}}],"isError":false}}}},"session_id":"fcc7566c-1234-5678-90ab-cdef12345678"}'
B3_RESULT_SUCCESS = b'{"type":"result","subtype":"success","duration_ms":2404,"is_error":false,"result":"5","session_id":"fcc7566c-1234-5678-90ab-cdef12345678","usage":{"inputTokens":14125,"outputTokens":111,"cacheReadTokens":28224,"cacheWriteTokens":0}}'
B3_NON_JSON = b'plain text line that is not JSON at all, with "quotes" and \\escapes'
B3_REJECTED = b'{"type":"tool_call","subtype":"completed","call_id":"tool_rej-001","tool_call":{"mcpToolCall":{"args":{"name":"vortex-foo","args":{},"toolName":"foo","providerIdentifier":"vortex"},"result":{"rejected":{"reason":"User rejected MCP: policy","isReadonly":false}}}},"session_id":"fcc7566c-1234-5678-90ab-cdef12345678"}'
B3_RESULT_ERROR_WITH_USAGE = b'{"type":"result","subtype":"success","duration_ms":10,"is_error":true,"result":"boom","session_id":"fcc7566c-1234-5678-90ab-cdef12345678","usage":{"inputTokens":10,"outputTokens":1,"cacheReadTokens":0,"cacheWriteTokens":0}}'


def test_snap_session_id_snake_case_and_gate() -> None:
    """session_id snap is snake_case and gated strictly on system+init."""
    sid = snap_session_id(B3_INIT)
    assert sid == "fcc7566c-1234-5678-90ab-cdef12345678"

    # Other lines that carry session_id must NOT yield it (gate enforced)
    assert snap_session_id(B3_TOOL_START) is None
    assert snap_session_id(B3_RESULT_SUCCESS) is None

    # Non-matching type/subtype
    other = b'{"type":"result","subtype":"success","session_id":"should-not-snap"}'
    assert snap_session_id(other) is None

    # Bad inputs never raise
    assert snap_session_id(b"not json") is None
    assert snap_session_id(b"") is None
    assert snap_session_id(b'{"not":"a session line"}') is None


def test_parse_tool_calls_nested_toolname_and_lifecycle() -> None:
    """Nested toolName extracted; subtype captured; order preserved."""
    blob = B3_TOOL_START + b"\n" + B3_TOOL_COMPLETED + b"\n" + B3_RESULT_SUCCESS
    recs = parse_tool_calls(blob)
    assert len(recs) == 2
    assert recs[0] == {"toolName": "tool_search", "subtype": "started"}
    assert recs[1] == {"toolName": "tool_search", "subtype": "completed"}


def test_parse_tool_calls_rejected_reason_no_raise() -> None:
    """Rejected result (with .result.rejected.reason) parses without raising."""
    recs = parse_tool_calls(B3_REJECTED)
    assert len(recs) == 1
    assert recs[0]["toolName"] == "foo"
    assert recs[0]["subtype"] == "completed"
    # (reason not surfaced in Phase 2; just must not explode on the shape)


def test_extract_usage_from_result_success_and_error_tolerated() -> None:
    """usage captured from result/success; is_error true does not raise."""
    u = extract_usage(B3_RESULT_SUCCESS)
    assert u is not None
    assert u["inputTokens"] == 14125
    assert u["outputTokens"] == 111
    assert u["cacheReadTokens"] == 28224
    assert u["cacheWriteTokens"] == 0

    u2 = extract_usage(B3_RESULT_ERROR_WITH_USAGE)
    assert u2 is not None
    assert u2["inputTokens"] == 10

    # Non-result or missing usage -> None, no raise
    assert extract_usage(B3_TOOL_START) is None
    assert extract_usage(b'{"type":"result","subtype":"success"}') is None


def test_non_json_line_verbatim_roundtrip_via_chunk() -> None:
    """Arbitrary non-JSON bytes (as decoded str) round-trip unchanged in data."""
    with tempfile.TemporaryDirectory() as td:
        sidecar = Path(td) / "test.ndjson"
        gaps: list[int] = [0]
        weird = 'raw log: {"partial": "json-looking but not really"} \n and "quotes"'
        _try_append_sidecar_chunk(
            str(sidecar),
            phase="stdout_chunk",
            data=weird,
            cap=SIDECAR_STDOUT_LINE_MAX,
            gaps=gaps,
        )
        assert gaps[0] == 0
        lines = sidecar.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["phase"] == "stdout_chunk"
        assert rec["data"] == weird  # verbatim, no mutation


def test_chunk_truncation_emits_len_and_kept() -> None:
    """Oversize line becomes <phase>_truncated with len + kept."""
    with tempfile.TemporaryDirectory() as td:
        sidecar = Path(td) / "trunc.ndjson"
        gaps: list[int] = [0]
        big = "x" * (SIDECAR_STDOUT_LINE_MAX + 100)
        _try_append_sidecar_chunk(
            str(sidecar),
            phase="stdout_chunk",
            data=big,
            cap=SIDECAR_STDOUT_LINE_MAX,
            gaps=gaps,
        )
        rec = json.loads(sidecar.read_text(encoding="utf-8").strip())
        assert rec["phase"] == "stdout_chunk_truncated"
        assert rec["len"] == len(big)
        assert rec["kept"] == SIDECAR_STDOUT_LINE_MAX
        assert len(rec["data"]) == SIDECAR_STDOUT_LINE_MAX
        assert rec["data"] == big[:SIDECAR_STDOUT_LINE_MAX]


def test_try_append_increments_gaps_on_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    """OSError on append increments the caller's gaps counter."""
    calls = {"n": 0}

    def boom(*a, **k):  # noqa: ANN001, ANN002
        calls["n"] += 1
        raise OSError("disk full")

    monkeypatch.setattr("cursorbuild.sidecar._append_sidecar", boom)
    gaps: list[int] = [0]
    _try_append_sidecar("/tmp/does-not-matter.ndjson", {"phase": "x"}, gaps)
    assert gaps[0] == 1


@pytest.mark.asyncio
async def test_capture_post_state_non_repo(tmp_path: Path) -> None:
    """Non-git dir yields audit_incomplete=True (never raises)."""
    status, diff, incomplete = await _capture_post_state(str(tmp_path))
    assert incomplete is True
    assert status == ""
    assert diff == ""
