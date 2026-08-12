"""L5 measurement hygiene — sidecar usage block model_label (7119)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    prepare_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_usage_sidecar import (
    render_usage_sidecar_section,
    stamp_usage_model_label,
    structured_closeout_has_usage_model_label,
)


def test_stamp_usage_model_label_beside_tokens() -> None:
    usage = stamp_usage_model_label(
        {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        "cursor/composer-2.5",
    )
    assert usage is not None
    assert usage["model_label"] == "cursor/composer-2.5"
    assert usage["total_tokens"] == 15


def test_render_usage_sidecar_section_consumer_shape() -> None:
    block = render_usage_sidecar_section(
        usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        usage_capture_status="captured",
        resolved_model="composer-2.5",
    )
    assert block is not None
    assert block.startswith("## usage\n")
    assert "model_label: composer-2.5" in block
    assert "total_tokens: 3" in block
    assert "usage_capture_status: captured" in block


def test_prepare_closeout_delivery_emits_usage_block_render_proof(
    tmp_path: Path,
) -> None:
    outcome = SdkRunOutcome(
        body="status: complete",
        status="finished",
        duration_ms=1000,
        tool_call_count=1,
        usage={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        usage_capture_status="captured",
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="auto-l5-usage-proof",
        outcome=outcome,
        degraded_reason=None,
        thread_id="7119",
        work_item_ref=None,
        resolved_model="cursor/composer-2.5",
    )
    sidecar_text = delivery.sidecar_path.read_text(encoding="utf-8")
    assert "## usage" in sidecar_text
    assert "model_label: cursor/composer-2.5" in sidecar_text
    assert "total_tokens: 18" in sidecar_text
    structured = json.loads(
        sidecar_text.split("## structured_closeout_full\n\n", 1)[1]
    )
    assert structured["usage"]["model_label"] == "cursor/composer-2.5"
    assert structured_closeout_has_usage_model_label(structured)
