"""Closeout memo bus pointer formatting."""

from __future__ import annotations

import hashlib
import re

import pytest

from closeout_memo.bus_pointer import compact_overflow_bus_body
from closeout_memo.models import CloseoutMemoRequest

pytestmark = pytest.mark.offline


def _memo() -> CloseoutMemoRequest:
    return CloseoutMemoRequest(
        memo_key="giw:lane-23222",
        kind="sdk_closeout",
        status="completed",
        wake_lane="23222",
        worker_thread="23222",
        dispatch_thread="23213",
        dispatch_id="disp-abc",
        execution_id="exec-def",
        sidecar="cortex://notes/closeout/h.md",
        emitted_at="2026-11-12T10:00:00Z",
    )


def test_compact_overflow_bus_body_section_map() -> None:
    overflow = "## summary\n\nDone.\n## tests\n\npytest ok\n"
    body = compact_overflow_bus_body(memo=_memo(), overflow_markdown=overflow)
    assert len(body) <= 2000
    assert "sha256:" in body
    assert "agent-bus:23222#closeout-overflow" in body
    assert "cortex://notes/closeout/h.md" not in body
    assert "## sections" in body


def test_compact_overflow_sha256_matches_overflow_without_sidecar_load() -> None:
    overflow = "<closeout_memo v=\"1\">\nlane: x\n</closeout_memo>\n"
    body = compact_overflow_bus_body(memo=_memo(), overflow_markdown=overflow)
    digest = hashlib.sha256(overflow.encode("utf-8")).hexdigest()
    match = re.search(r"sha256: `([0-9a-f]{64})`", body)
    assert match is not None
    assert match.group(1) == digest


def test_c6_coalesced_multi_memo_sha_matches_overflow_ref() -> None:
    overflow = "joined overflow bytes\n"
    body = compact_overflow_bus_body(
        memo=_memo(),
        overflow_markdown=overflow,
        coalesced_count=3,
    )
    digest = hashlib.sha256(overflow.encode("utf-8")).hexdigest()
    match = re.search(r"sha256: `([0-9a-f]{64})`", body)
    assert match is not None
    assert match.group(1) == digest
    assert "cortex://notes/closeout/h.md" not in body
    assert "agent-bus:23222#closeout-overflow" in body


def test_w1_no_cortex_cite_without_sidecar_markdown() -> None:
    overflow = "overflow-only"
    body = compact_overflow_bus_body(memo=_memo(), overflow_markdown=overflow)
    assert "cortex://notes/closeout/h.md" not in body
    digest = hashlib.sha256(overflow.encode("utf-8")).hexdigest()
    match = re.search(r"sha256: `([0-9a-f]{64})`", body)
    assert match is not None
    assert match.group(1) == digest


def test_compact_overflow_uses_cortex_uri_when_sidecar_markdown_supplied() -> None:
    overflow = "overflow-only"
    stored = "## real closeout\n\nbytes at cortex\n"
    body = compact_overflow_bus_body(
        memo=_memo(),
        overflow_markdown=overflow,
        sidecar_markdown=stored,
    )
    digest = hashlib.sha256(stored.encode("utf-8")).hexdigest()
    match = re.search(r"sha256: `([0-9a-f]{64})`", body)
    assert match is not None
    assert match.group(1) == digest
    assert "cortex://notes/closeout/h.md" in body
