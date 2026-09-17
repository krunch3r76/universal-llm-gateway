"""Regression: the admission outcome must cross the wire, not the heartbeat.

Six dispatch lanes were lost because ``/enqueue`` answered with a live-handler
reading ~30ms before the job terminal-failed on an admit gate, and the envelope
was indistinguishable from a job that ran:

* ``11508``, ``11509`` — 2026-09-16, assertion ``a:34799``
* ``11581`` r2, ``11584`` r1, ``11585`` r1 — 2026-09-17, assertion ``a:35275``

CHECKPOINT #17 on ``agent-bus:10479`` recorded three of them as armed and
non-terminal on the strength of that envelope. These tests assert the single
synchronous response now distinguishes a refused job from an admitted one, and
that ``auto_handler_status`` never again carries a job verdict.

Spec: ``cortex://notes/system/specs/agent-bus-request-admission-outcome-on-wire.md``
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_auto.fix_hints import (
    PROPAGATE_BLOCK_INVALID_FIX_HINT,
    PROPAGATE_MISSING_FIX_HINT,
)
from services.git_integration_worker.cursor_auto.liveness import get_registry


@pytest.fixture
def cursor_auto_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return TestClient(create_app())


def _enqueue(client: TestClient, *, body: str, thread_id: str) -> dict[str, Any]:
    get_registry().register(f"{thread_id}-handler")
    resp = client.post(
        "/api/v1/git/cursor-auto/enqueue",
        json={
            "thread_id": thread_id,
            "turn_number": 1,
            "subject": "admission outcome regression",
            "body": body,
            "from_agent": "mcp-server",
            "to_agent": "cursor",
            "desired_model": "auto",
            "desired_effort": "medium",
            "contract": "implement",
            "request_id": f"rid-{thread_id}",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# No body-level ``contract:`` line: a contract override plus a roaming-tier
# model waives the empty-scope refusal, and a waiver is not the regression.
_EMPTY_SCOPE_BODY = "TYPE: DIRECTIVE\nvision: [universal:events]\n"
_SCOPED_BODY = (
    "TYPE: DIRECTIVE\n"
    "vision: [universal:events]\n"
    "files_expected: services/git_integration_worker/routes/cursor_auto.py\n"
    "scope: put the admission outcome on the wire\n"
    "AC: the synchronous response distinguishes refused from admitted\n"
)


def test_empty_scope_request_is_distinguishable_in_one_response(
    cursor_auto_client: TestClient,
) -> None:
    """The 11508/11509/11581/11584/11585 regression, in a single read."""
    refused = _enqueue(cursor_auto_client, body=_EMPTY_SCOPE_BODY, thread_id="11508")
    admitted = _enqueue(cursor_auto_client, body=_SCOPED_BODY, thread_id="11581")

    # Both are "armed" by the old reading — the handler is live for each.
    assert refused["auto_handler_status"] == "auto-handler-live"
    assert admitted["auto_handler_status"] == "auto-handler-live"
    assert refused["ok"] is True and admitted["ok"] is True

    # The job verdict is what separates them, and it is present synchronously.
    assert refused["job_admission"]["outcome"] == "refused"
    assert refused["job_admission"]["reason"] == "empty_directive_scope"
    assert admitted["job_admission"]["outcome"] == "deferred"
    assert refused["job_admission"]["outcome"] != admitted["job_admission"]["outcome"]


def test_refusal_carries_fix_hint_and_missed_tokens(
    cursor_auto_client: TestClient,
) -> None:
    """AC3: recovering the reason costs zero extra reads."""
    body = _enqueue(cursor_auto_client, body=_EMPTY_SCOPE_BODY, thread_id="11509")
    admission = body["job_admission"]
    assert admission["fix_hint"]
    assert isinstance(admission["missed_tokens"], list)
    assert admission["missed_tokens"]


def test_projection_qualifies_its_own_coverage(
    cursor_auto_client: TestClient,
) -> None:
    """AC2: thread-state gates report deferred, never admitted."""
    body = _enqueue(cursor_auto_client, body=_SCOPED_BODY, thread_id="11584")
    admission = body["job_admission"]
    assert admission["outcome"] == "deferred"
    assert admission["coverage"]["deferred"] == [
        "thread_terminal_status",
        "relay_trust",
        "synthesized_closeout_ack",
        "auth_gate_budget",
    ]
    assert "empty_directive_scope" in admission["coverage"]["asserted"]
    assert admission["source"] == "cursor_auto.admit_gates.blocking_admit_gate"
    assert admission["recovery"] == "agent_bus_read(job_state)"
    assert admission["scope"] == "request_id:rid-11584"
    assert admission["as_of"]


def test_no_live_handler_states_no_job_was_admitted(
    cursor_auto_client: TestClient,
) -> None:
    """AC2: present on every return path, including the degraded ones."""
    registry = get_registry()
    for handler_id in list(registry.snapshot().get("handlers") or []):
        registry.unregister(str(handler_id))
    assert not registry.is_live()
    resp = cursor_auto_client.post(
        "/api/v1/git/cursor-auto/enqueue",
        json={
            "thread_id": "11585",
            "turn_number": 1,
            "subject": "no handler",
            "body": _SCOPED_BODY,
            "from_agent": "mcp-server",
            "contract": "implement",
        },
    )
    assert resp.status_code == 503, resp.text
    payload = resp.json()
    assert payload["auto_handler_status"] == "no-auto-handler"
    assert payload["job_admission"]["outcome"] == "not_applicable"
    assert payload["job_admission"]["reason"] == "no_live_auto_handler"


_PROPAGATION_INVALID_PROOF_CLASS_BODY = """\
TYPE: DIRECTIVE
contract: propagate
effects_expected: row persisted

## propagation
```yaml
propagation:
  - service: mcp
    code_ref: d3e17d54
    safe_window: standalone_ok
    proof_class: served_artifact
```
"""


def test_propagation_block_invalid_refusal_projects_invalid_flags(
    cursor_auto_client: TestClient,
) -> None:
    """AC1 (a:35369): caller sees which propagation field was rejected."""
    get_registry().register("11626-handler")
    resp = cursor_auto_client.post(
        "/api/v1/git/cursor-auto/enqueue",
        json={
            "thread_id": "11626",
            "turn_number": 1,
            "subject": "propagation invalid proof_class",
            "body": _PROPAGATION_INVALID_PROOF_CLASS_BODY,
            "from_agent": "mcp-server",
            "to_agent": "cursor",
            "desired_model": "auto",
            "desired_effort": "medium",
            "contract": "propagate",
            "request_id": "rid-11626",
        },
    )
    assert resp.status_code == 200, resp.text
    admission = resp.json()["job_admission"]
    assert admission["outcome"] == "refused"
    assert admission["reason"] == "propagation_block_invalid"
    assert isinstance(admission["invalid_flags"], list)
    assert any(
        "invalid_proof_class:served_artifact" in flag
        for flag in admission["invalid_flags"]
    )
    assert admission["fix_hint"] == PROPAGATE_BLOCK_INVALID_FIX_HINT
    assert admission["fix_hint"] != PROPAGATE_MISSING_FIX_HINT


def test_retired_tokens_are_gone_from_every_enqueue_path(
    cursor_auto_client: TestClient,
) -> None:
    """AC1 [universal:no-bc]: no alias, no shim — old key and value deleted."""
    admitted = _enqueue(cursor_auto_client, body=_SCOPED_BODY, thread_id="11585")
    assert "handler_status" not in admitted
    assert "auto-admit-armed" not in str(admitted)
