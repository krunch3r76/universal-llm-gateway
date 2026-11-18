"""Tests for generate_admission_context_store migration columns."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from systems.frontier_consult.generate_admission_context_store import (
    read_admission_context,
    reset_generate_admission_stores_for_tests,
    write_admission_context,
)


@pytest.fixture(autouse=True)
def _clean_store() -> None:
    reset_generate_admission_stores_for_tests()


@pytest.mark.offline
def test_resolved_effort_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    write_admission_context(
        execution_id="exec-1",
        auto_review_child=False,
        op="generate",
        role="cursor-sdk",
        resolved_model="cursor/claude-sonnet-5",
        parent_dispatch_thread_id=None,
        dispatch_thread_id=None,
        resolved_effort="low",
    )
    ctx = read_admission_context("exec-1")
    assert ctx is not None
    assert ctx.resolved_effort == "low"

    write_admission_context(
        execution_id="exec-2",
        auto_review_child=False,
        op="generate",
        role="cursor-sdk",
        resolved_model="cursor/claude-sonnet-5",
        parent_dispatch_thread_id=None,
        dispatch_thread_id=None,
    )
    ctx2 = read_admission_context("exec-2")
    assert ctx2 is not None
    assert ctx2.resolved_effort is None


@pytest.mark.offline
def test_frozen_instruction_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    write_admission_context(
        execution_id="exec-frozen",
        auto_review_child=False,
        op="generate",
        role="cursor-sdk",
        resolved_model="cursor/claude-sonnet-5",
        parent_dispatch_thread_id=None,
        dispatch_thread_id=None,
        frozen_instruction_text="abc",
    )
    ctx = read_admission_context("exec-frozen")
    assert ctx is not None
    assert ctx.frozen_instruction_text == "abc"
    assert ctx.frozen_instruction_sha256 == hashlib.sha256(b"abc").hexdigest()

    write_admission_context(
        execution_id="exec-frozen-none",
        auto_review_child=False,
        op="generate",
        role="cursor-sdk",
        resolved_model="cursor/claude-sonnet-5",
        parent_dispatch_thread_id=None,
        dispatch_thread_id=None,
    )
    ctx2 = read_admission_context("exec-frozen-none")
    assert ctx2 is not None
    assert ctx2.frozen_instruction_text is None
    assert ctx2.frozen_instruction_sha256 is None
