"""Golden tests for resume bundle manifest derivation."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from agent_bus_store.db import create_thread, create_turn, init_db
from agent_bus_store.resume_fence import arm_resume_fence, assemble_resume_fence
from agent_bus_store.resume_fence_store import fold_fence

pytestmark = pytest.mark.offline

_TIP_BODY = """
## Derived zone
### Child lanes
agent-bus:10450 · unassociated · active · turn 3
agent-bus:10303 · unassociated · active · turn 9

### Cited lanes
agent-bus:6341 · cited · closed · turn 2

### Artifact anchors
cortex://notes/system/specs/example.md git:40869be742606d30f4480b810213b22c39b060f4

Window: transcript_id=d556c84f-a1b2-c3d4-e5f6-7890abcdef12 turns@cp=120
"""

_CARD = """
## Sidecars
cortex://notes/system/threads/10223-opportunities.md

## Pools
<!-- pools v1 sha256:abc -->
| pool | executor | status | must_load | must_read | closeout | forbidden |
| --- | --- | --- | --- | --- | --- | --- |
| orchestrator | cursor | open | ulg-for-llms | tip CP | agent-bus:10303 | posts on 10223 |
"""


@pytest.fixture()
def root_thread(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    init_db()
    create_thread(thread_id="10223", slug="continuity", tags=["role:root"])
    create_turn(
        thread_id="10223",
        from_agent="cursor",
        to_agent="cursor",
        subject="CHECKPOINT",
        body=_TIP_BODY,
        status="open",
    )
    yield "10223"


def test_arm_resume_fence_journals_armed_only(root_thread) -> None:
    with (
        patch(
            "agent_bus_store.resume_fence.load_continuity_card",
            return_value=_CARD,
        ),
    ):
        payload = arm_resume_fence("10223", transcript_id="tab-x", source="hook_prompt")
    assert payload["fence"]["state"] == "armed"
    assert payload["fence_carriage"]["fence_id"] == payload["fence"]["fence_id"]
    assert "continuity(op=resume" in payload["fence_carriage"]["first_hop"]
    assert "tape_verbal" not in json.dumps(payload)
    mcp_allow = payload["read_set"]["readable"]["mcp_allow"]
    assert not any(r.get("tool") == "GetDynamicTools" for r in mcp_allow)


def test_assemble_resume_fence_manifest_excludes_9796(root_thread) -> None:
    envelope = {
        "scope": "last_session",
        "seal_status": "sealed",
        "tape_verbal": [],
        "checkpoint_highlight": "highlight",
        "consolidate_summary_row": "row",
        "summary_row_source": "l3",
        "summary_row_as_of_turn": 507,
    }
    with (
        patch(
            "agent_bus_store.resume_fence.build_resume_envelope",
            return_value=envelope,
        ),
        patch(
            "agent_bus_store.resume_fence.load_continuity_card",
            return_value=_CARD,
        ),
        patch(
            "agent_bus_store.resume_fence.resolve_code_version",
            return_value="abc123",
        ),
    ):
        bundle = assemble_resume_fence("10223", transcript_id="tab-x", source="test")

    assert bundle["tip_checkpoint"]["turn_number"] == 1
    assert bundle["resume_envelope"]["seal_status"] == "sealed"
    assert "mission" in bundle
    assert "tape" in bundle
    mcp_allow = bundle["read_set"]["readable"]["mcp_allow"]
    continuity = next(r for r in mcp_allow if r["tool"] == "continuity")
    assert "tape_read" in continuity["ops"]
    assert bundle["read_set"]["readable"]["bus_threads"] == ["10223"]
    citable_threads = bundle["read_set"]["citable"]["bus_threads"]
    assert "10223" in citable_threads
    assert "10450" in citable_threads
    assert "9796" not in citable_threads
    blob = json.dumps(bundle).lower()
    assert "grok" not in blob
    assert "9796" not in blob
    assert bundle["fence"]["state"] == "released"
    assert bundle["fence_carriage"]["fence_id"] == bundle["fence"]["fence_id"]
    assert bundle["mission"]["fence_id"] == bundle["fence"]["fence_id"]
    assert not any(
        r.get("tool") == "GetDynamicTools"
        for r in bundle["read_set"]["readable"]["mcp_allow"]
    )
    folded = fold_fence(bundle["fence"]["fence_id"])
    assert folded is not None
    assert folded.state == "released"
