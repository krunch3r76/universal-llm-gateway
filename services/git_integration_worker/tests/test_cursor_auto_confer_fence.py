"""Hermetic tests for confer write-fence and capture honesty relay."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

from services.git_integration_worker.cursor_auto.closeout_relay import (
    looks_section2,
    machine_write_uris,
    select_closeout_relay_payload,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex import (
    guard_matches_write,
)
from services.git_integration_worker.cursor_auto.directive import (
    build_sdk_message,
    corpus_guard_uris,
    parse_request_body,
)
from services.git_integration_worker.cursor_auto.nested_outcome import (
    relay_confer_outcome,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob

_OFFGIT_URI = (
    "cortex://notes/system/threads/friction-26462-cdp-explore/consult-brief.md"
)
_SIBLING_URI = (
    "cortex://notes/system/threads/friction-26462-cdp-explore/confer-report.md"
)
_GUARD_DIR = "cortex://notes/system/threads/friction-26462-cdp-explore/"


def _confer_wrapper(
    *,
    offgit: list[str] | None = None,
    fs_only: bool = False,
    oob_deviation: bool = False,
) -> str:
    """Build wrapper manifest resembling auto-1181167b84c3."""
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": "complete",
        "summary": "dispatch auto-1181167b84c3",
        "files_created": [],
        "files_modified": [],
        "files_deleted": [],
        "effects": [],
        "capture_status": "partial",
        "effects_manifest": {"schema_version": 1, "surfaces": {}},
    }
    if offgit is not None:
        payload["files_offgit_produced"] = offgit
    if fs_only:
        payload["files_offgit_produced"] = []
        payload["effects_manifest"] = {
            "schema_version": 1,
            "surfaces": {
                "fs": {
                    "surface": "fs",
                    "source": "mcp",
                    "entries": [
                        {
                            "target": _OFFGIT_URI,
                            "detail": {
                                "op": "write",
                                "sandbox": "cortex",
                                "path": _OFFGIT_URI.removeprefix("cortex://"),
                            },
                        }
                    ],
                }
            },
        }
    if oob_deviation:
        payload["files_offgit_produced"] = []
        payload["deviations"] = [f"capture:oob_cortex_write_unobserved:{_OFFGIT_URI}"]
    return json.dumps(payload)


def _directive_body(*, include_guard: bool = True) -> str:
    lines = [
        "TYPE: DIRECTIVE",
        "density: dense",
        "contract: confer",
    ]
    if include_guard:
        lines.append(f"evidence_required: `{_OFFGIT_URI}` · sha256:abc123")
    return "\n".join(lines)


def test_machine_write_uris_unions_fs_and_oob():
    fs_wrapper = _confer_wrapper(fs_only=True)
    oob_wrapper = _confer_wrapper(oob_deviation=True)
    assert _OFFGIT_URI in machine_write_uris(fs_wrapper)
    assert _OFFGIT_URI in machine_write_uris(oob_wrapper)


def test_confer_fixture_lists_offgit_uri_not_effects_none():
    wrapper = _confer_wrapper(offgit=[_OFFGIT_URI])
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=None,
        ledger_status="completed",
        dispatch_id="auto-1181167b84c3",
    )
    assert looks_section2(payload.body)
    assert _OFFGIT_URI in payload.body
    assert "effects: (none" not in payload.body.lower()
    assert payload.status != "complete"


def test_fence_violation_when_guarded_write_unannounced():
    wrapper = _confer_wrapper(offgit=[_OFFGIT_URI])
    guard = frozenset({_OFFGIT_URI})
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=None,
        ledger_status="completed",
        dispatch_id="auto-1181167b84c3",
        guard_uris=guard,
    )
    assert "fence_violation:" in payload.body.lower()
    assert payload.status in {"blocked", "partial"}


def test_ac2b_fs_only_oob_fixture_fence_violation():
    wrapper = _confer_wrapper(fs_only=True)
    guard = frozenset({_OFFGIT_URI})
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=None,
        ledger_status="completed",
        dispatch_id="auto-oob-only",
        guard_uris=guard,
    )
    assert _OFFGIT_URI in payload.body
    assert "fence_violation:" in payload.body.lower()
    assert payload.status != "complete"


def test_no_fence_when_write_outside_guard():
    wrapper = _confer_wrapper(offgit=[_SIBLING_URI])
    guard = frozenset({_OFFGIT_URI})
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=None,
        ledger_status="completed",
        guard_uris=guard,
    )
    assert "fence_violation:" not in payload.body.lower()


def test_fence_exception_suppresses_violation():
    wrapper = _confer_wrapper(offgit=[_OFFGIT_URI])
    guard = frozenset({_OFFGIT_URI})
    sidecar = f"""\
TYPE: CLOSEOUT
status: complete

**ac_verdict:** consulted corpus

**deltas_to_spec:** none

fence_exception: {_OFFGIT_URI} — operator-authorized refresh

**effects:** (none — confer contract; no repo writes)
"""
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=sidecar,
        ledger_status="completed",
        guard_uris=guard,
    )
    assert "fence_violation:" not in payload.body.lower()


def test_guard_directory_prefix_matches_nested_write():
    assert guard_matches_write(_GUARD_DIR, _OFFGIT_URI) is True
    assert guard_matches_write(_OFFGIT_URI, _SIBLING_URI) is False


def test_parse_request_body_harvests_evidence_required_uris():
    body = (
        "TYPE: DIRECTIVE\n"
        f"evidence_required: `{_OFFGIT_URI}` · sha256:deadbeef\n"
        f"read-corpus: {_SIBLING_URI},"
    )
    parsed = parse_request_body(body)
    assert parsed is not None
    assert _OFFGIT_URI in parsed.evidence_required_uris
    assert _SIBLING_URI in parsed.evidence_required_uris
    assert corpus_guard_uris(parsed) == frozenset({_OFFGIT_URI})


def test_build_sdk_message_includes_forbidden_paths_for_confer():
    message = build_sdk_message(_directive_body(), contract="confer")
    assert "Forbidden durable write targets" in message
    assert _OFFGIT_URI in message


def test_relay_confer_wake_and_envelope_non_complete_on_fence(monkeypatch):
    wrapper = _confer_wrapper(offgit=[_OFFGIT_URI])
    captured: dict[str, str] = {}

    async def _fake_wake(job, *, dispatch_id, request_turn, closeout_status, bus=None):
        captured["wake_status"] = closeout_status
        return {"ok": True, "status_code": 200}

    async def _fake_confer(job, *, dispatch_id, model_id, status, closeout_body, bus=None):
        captured["confer_status"] = status
        captured["confer_body"] = closeout_body
        return {"ok": True, "status_code": 200}

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_wake",
        _fake_wake,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.post_operator_confer",
        _fake_confer,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.read_repo_closeout_sidecar",
        lambda _dispatch_id: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_outcome.append_journal_entry",
        lambda **kwargs: None,
    )

    queue = MagicMock()
    job = AutoJob(
        job_id="j-confer-fence",
        thread_id="5992",
        turn_number=3,
        subject="confer fence",
        body=_directive_body(),
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="confer",
    )
    result = asyncio.run(
        relay_confer_outcome(
            job,
            client=MagicMock(),
            queue=queue,
            dispatch_id="auto-1181167b84c3",
            model={"resolved_model_id": "cursor/composer-2.5"},
            effort={},
            gate_plan={},
            sdk_body=wrapper,
            terminal_status="completed",
        )
    )
    assert captured["confer_status"] != "complete"
    assert captured["wake_status"] != "complete"
    assert "fence_violation:" in captured["confer_body"].lower()
    assert result["closeout_status"] != "complete"
