"""Tests for implement-closeout trigger source_ref normalization (friction-26765)."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.scheme_resolve import resolve_schemed_packet_file
from implement_admission.source_ref import parse_source_ref
from implement_admission.spec import SourceKind
from services.git_integration_worker.cursor_sdk_closeout_trigger import (
    build_closeout_trigger_payload,
    normalize_closeout_source_ref,
)
from services.git_integration_worker.cursor_sdk_deliverables import sidecar_workspaces_ref


def test_normalize_sidecar_to_packet() -> None:
    dispatch_id = "abc123-closeout"
    sidecar = sidecar_workspaces_ref(dispatch_id)
    normalized = normalize_closeout_source_ref(sidecar)
    assert normalized == f"packet:tmp/reviews/closeouts/{dispatch_id}.md"
    assert not normalized.startswith("packet:workspaces://")
    ref = parse_source_ref(normalized)
    assert ref.source_kind == SourceKind.PACKET.value


def test_normalize_todo_passthrough() -> None:
    assert normalize_closeout_source_ref("todo:friction-26765") == "todo:friction-26765"


def test_build_closeout_trigger_payload_uses_normalized_ref() -> None:
    sidecar = sidecar_workspaces_ref("d1")
    payload = build_closeout_trigger_payload(
        body_json='{"schema_version":1,"status":"complete","summary":"ok"}',
        source_ref=normalize_closeout_source_ref(sidecar),
        idempotency_key="k1",
    )
    assert payload["source_ref"].startswith("packet:tmp/reviews/closeouts/")


def test_normalized_packet_resolves_sidecar_file(tmp_path: Path) -> None:
    repo = tmp_path / "universal-llm-gateway"
    rel = "tmp/reviews/closeouts/d-adapter.md"
    sidecar = repo / rel
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("source_ref: todo:friction-1\n", encoding="utf-8")

    packet_ref = normalize_closeout_source_ref(sidecar_workspaces_ref("d-adapter"))
    parse_source_ref(packet_ref)
    resolved = resolve_schemed_packet_file(
        packet_ref.removeprefix("packet:"),
        workspaces_root_override=tmp_path,
    )
    assert resolved == sidecar.resolve()
