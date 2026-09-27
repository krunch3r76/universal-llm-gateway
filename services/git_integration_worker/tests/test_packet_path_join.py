"""Regression: GIW packet_path join must not double basename(source_repo).

Friction a:36662 — a relative packet path that already starts with the source
repo basename 422s CURSOR_PACKET_INVALID when joined onto that repo root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.git_integration_worker.models.cursor_api import CursorDispatchRequest
from services.git_integration_worker.routes.cursor_sdk import _read_packet_text

# Exact pair from a:36662.
_PACKET_REL = (
    "universal-llm-gateway/tmp/implement-admission/materialized/"
    "conductor-auto-conductor-parallel-admit.md"
)
_INNER_REL = (
    "tmp/implement-admission/materialized/conductor-auto-conductor-parallel-admit.md"
)


def _req(packet_path: str) -> CursorDispatchRequest:
    return CursorDispatchRequest(
        thread_id="t-36662",
        model="cursor/grok-4.7",
        dispatch_id="d-36662",
        execution_id="e-36662",
        packet_path=packet_path,
    )


def _source_with_packet(tmp_path: Path) -> Path:
    source = tmp_path / "universal-llm-gateway"
    packet = source / _INNER_REL
    packet.parent.mkdir(parents=True)
    packet.write_text("conductor-packet\n", encoding="utf-8")
    return source


def test_read_packet_text_strips_source_repo_basename_prefix(
    tmp_path: Path,
) -> None:
    source = _source_with_packet(tmp_path)
    text = _read_packet_text(_req(_PACKET_REL), source)
    assert text == "conductor-packet\n"
    assert (source / _INNER_REL).is_file()


def test_read_packet_text_strips_workspaces_scheme(tmp_path: Path) -> None:
    source = _source_with_packet(tmp_path)
    uri = f"workspaces://{_PACKET_REL}"
    text = _read_packet_text(_req(uri), source)
    assert text == "conductor-packet\n"


def test_read_packet_text_bare_rel_unchanged(tmp_path: Path) -> None:
    source = _source_with_packet(tmp_path)
    text = _read_packet_text(_req(_INNER_REL), source)
    assert text == "conductor-packet\n"


def test_read_packet_text_missing_after_strip_raises(tmp_path: Path) -> None:
    source = tmp_path / "universal-llm-gateway"
    source.mkdir()
    with pytest.raises(ValueError, match="packet_path not found"):
        _read_packet_text(_req(_PACKET_REL), source)


def test_read_packet_text_traversal_still_rejected(tmp_path: Path) -> None:
    source = tmp_path / "universal-llm-gateway"
    source.mkdir()
    with pytest.raises(ValueError, match="workspaces-relative"):
        _read_packet_text(_req("../etc/passwd"), source)
