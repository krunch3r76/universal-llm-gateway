"""GIW closeout grade: conductor unwitnessed DONE claims."""

from __future__ import annotations

import json
from pathlib import Path

from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    prepare_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_closeout.degraded_reasons import (
    conductor_unwitnessed_done_degraded_reason,
)

_CONDUCTOR_PACKET = """\
---
packet_kind: conductor
work_key: todo:entity-private-id-mutable-name
contract: light-bounded
lane: B
---
<scope>Conductor session.</scope>
"""

_G1_DONE_NO_EDGE = """\
| G1 | Architecture consult | DONE |
status: complete
"""

_G1_DONE_WITH_EDGE_BODY = """\
| G1 | Architecture consult | DONE |
status: complete
"""


class _EdgeCortex:
    def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN003, ANN201
        if entity_id.startswith("document:"):
            return {"id": entity_id, "attributes": {"consult_kind": "architecture"}}
        return {"id": entity_id, "attributes": {"density_triage": "judgment_required"}}

    def list_relationships(self, entity_id: str, *, type_id: str | None = None):  # noqa: ARG002
        return [
            {
                "id": 99,
                "source_id": "todo:entity-private-id-mutable-name",
                "target_id": "document:arch",
                "type_id": "derived_from",
            }
        ]


class _NoRelCortex(_EdgeCortex):
    def list_relationships(self, entity_id: str, *, type_id: str | None = None):  # noqa: ARG002
        return []


def test_unwitnessed_g1_done_degrades(monkeypatch) -> None:
    monkeypatch.setattr(
        "implement_admission.conductor_witness_defaults.DefaultWitnessCortex",
        _NoRelCortex,
    )
    reason = conductor_unwitnessed_done_degraded_reason(
        body=_G1_DONE_NO_EDGE,
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
    )
    assert reason == "unwitnessed_done_claim"


def test_witnessed_g1_done_not_degraded(monkeypatch) -> None:
    monkeypatch.setattr(
        "implement_admission.conductor_witness_defaults.DefaultWitnessCortex",
        lambda: _EdgeCortex(),
    )
    reason = conductor_unwitnessed_done_degraded_reason(
        body=_G1_DONE_WITH_EDGE_BODY,
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
    )
    assert reason is None


def test_prepare_closeout_unwitnessed_done_partial(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "implement_admission.conductor_witness_defaults.DefaultWitnessCortex",
        _NoRelCortex,
    )
    degraded = conductor_unwitnessed_done_degraded_reason(
        body=_G1_DONE_NO_EDGE,
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
    )
    assert degraded == "unwitnessed_done_claim"
    outcome = SdkRunOutcome(
        body=_G1_DONE_NO_EDGE,
        status="finished",
        duration_ms=100,
        tool_call_count=3,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-unwitnessed",
        outcome=outcome,
        degraded_reason=degraded,
        thread_id="t-unwitnessed",
        work_item_ref="todo:entity-private-id-mutable-name",
        packet_text=_CONDUCTOR_PACKET,
    )
    payload = json.loads(delivery.body)
    assert payload["status"] == "partial"
    assert payload["degraded_reason"] == "unwitnessed_done_claim"
