"""R6 contract keys and 4KB cap."""

from __future__ import annotations

import json

import pytest

from operator_hop_harvest.assemble import (
    assemble_operator_hop_view,
    cap_view_json_bytes,
)

pytestmark = pytest.mark.offline

_REQUIRED_KEYS = {
    "worker_thread",
    "summoning_thread",
    "conductor",
    "scoreboard",
    "wait",
    "job",
    "harvest_recipe",
    "continuation",
    "next_admit_divergent",
}


def test_assemble_has_r6_keys() -> None:
    view = assemble_operator_hop_view(
        worker_thread={"id": "12291"},
        summoning_thread={"id": "12286"},
        conductor={"dispatch_id": "x"},
        scoreboard={"uri": "cortex://x"},
        wait={"open": False, "kind": "none"},
        job=None,
        harvest_recipe={"threads": []},
        continuation={"auto": False, "reason": "test"},
        next_admit_divergent=False,
    )
    assert _REQUIRED_KEYS <= set(view.keys())


def test_cap_view_under_4kb() -> None:
    view = assemble_operator_hop_view(
        worker_thread={"id": "12291"},
        summoning_thread={"id": "12286"},
        conductor={"dispatch_id": "x", "stop_tokens": ["ROW_HOP"]},
        scoreboard={"uri": "cortex://x", "rows": [{"id": f"G{i}"} for i in range(50)]},
        wait={"open": False, "kind": "none"},
        job=None,
        harvest_recipe={"threads": []},
        continuation={"auto": False, "reason": "test"},
        next_admit_divergent=False,
    )
    capped = cap_view_json_bytes(view)
    assert len(json.dumps(capped, ensure_ascii=False).encode("utf-8")) <= 4096
