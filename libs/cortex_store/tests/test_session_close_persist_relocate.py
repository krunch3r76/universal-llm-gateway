"""Session-close persist guards for raw transcript relocate (M1/M3)."""

from __future__ import annotations

import inspect

import pytest

from cortex_store.routes.session_close_persist import manifest_source_uri_from_relocate
from cortex_store.routes.session_close_source_relocate import SourceRelocateResult

pytestmark = pytest.mark.offline


def test_m1_relocate_runs_after_seal_and_transcript_writes() -> None:
    from cortex_store.routes import session_close_persist as persist

    source = inspect.getsource(persist.persist_session_close)
    seal_idx = source.index("seal_abs_path, seal_payload")
    transcript_idx = source.index("durable_write_text(abs_path, ctx.composed_md")
    relocate_idx = source.index("relocate_transcript_source(")
    assert seal_idx < transcript_idx < relocate_idx


def test_m3_manifest_source_uri_none_without_relocate() -> None:
    assert manifest_source_uri_from_relocate(None) is None
    partial = SourceRelocateResult(prior_snapshot=None, written_sha256="0" * 64)
    assert manifest_source_uri_from_relocate(partial) is None


def test_m3_manifest_source_uri_when_relocate_succeeded() -> None:
    result = SourceRelocateResult(
        prior_snapshot=None,
        written_sha256="abc",
        manifest_rel_path="notes/system/transcripts/_nohouse/cursor/raw/2026-01-01/id.manifest.json",
    )
    assert manifest_source_uri_from_relocate(result) == (
        "files://notes/system/transcripts/_nohouse/cursor/raw/2026-01-01/id.manifest.json"
    )
