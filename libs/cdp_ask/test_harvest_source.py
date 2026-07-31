"""Offline tests for harvest-source wiring and F6 content_proof ordering."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from claude_bundles.cowork_output_download import should_attempt_output_download
from claude_bundles.project_ask import (
    HarvestArchiveError,
    archive_harvest,
    read_archive_execution_id,
)

from cdp_ask.models import ExecutionPollResponse, SubmitProjectAskRequest
from cdp_ask.page_liveness import (
    LadderAdvanceState,
    LadderCallbacks,
    advance_ladder_from_harvest,
)
from cdp_ask.runner import default_archive_path, resolve_content_proof_targets

pytestmark = pytest.mark.offline

_EXEC_ID = "exec" + "a" * 28


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _stamped_archive(path: Path, body: str, execution_id: str) -> None:
    archive_harvest(
        body=body,
        url="https://claude.ai/new",
        project_uuid="",
        model={"ok": True},
        attested_model="opus-4.8",
        archive_path=str(path),
        execution_id=execution_id,
    )


def test_submit_request_harvest_defaults() -> None:
    req = SubmitProjectAskRequest(prompt_text="hello")
    assert req.expected_size == "auto"
    assert req.harvest_source == "auto"
    assert req.download_output is False


def test_should_attempt_for_large_request() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="hello",
        expected_size="large",
    )
    assert should_attempt_output_download(
        harvest_source=req.harvest_source,
        expected_size=req.expected_size,
        download_output=req.download_output,
    )


def test_small_never_attempts_download() -> None:
    assert not should_attempt_output_download(
        harvest_source="auto",
        expected_size="small",
        download_output=True,
    )


def test_archive_stamp_identity_after_download_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    archive = tmp_path / "notes/system/threads/cdp-ask-archive-new-abcdef12.md"
    downloaded = "x" * 800
    _stamped_archive(archive, downloaded, _EXEC_ID)
    assert read_archive_execution_id(str(archive)) == _EXEC_ID
    assert _sha256_file(archive) == _sha256_file(archive)
    assert len(archive.read_text(encoding="utf-8")) > 700


@pytest.mark.asyncio
async def test_f6_thin_archive_blocked_while_output_download_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    req = SubmitProjectAskRequest(
        prompt_text="hello",
        expected_size="large",
        harvest_source="auto",
    )
    execution_id = _EXEC_ID
    archive = Path(default_archive_path(req, execution_id=execution_id))
    _stamped_archive(archive, "thin chat card", execution_id)
    targets = resolve_content_proof_targets(req, execution_id=execution_id)
    events: list[str] = []

    async def on_content_proof(uri: str, sha: str) -> None:
        events.append(f"content_proof:{uri}")

    progress = LadderAdvanceState(
        targets=targets,
        min_bytes=40,
        sha256_file=_sha256_file,
        execution_id=execution_id,
        output_download_pending=True,
        blocked_archive_paths={archive.resolve()},
        turn_idle_sent=True,
    )
    callbacks = LadderCallbacks(on_content_proof=on_content_proof)
    await advance_ladder_from_harvest(
        {
            "streaming": False,
            "stop": False,
            "tool_pause": False,
            "body_len": 100,
        },
        callbacks=callbacks,
        progress=progress,
    )
    assert events == []

    progress.output_download_pending = False
    _stamped_archive(archive, "x" * 500, execution_id)
    await advance_ladder_from_harvest(
        {
            "streaming": False,
            "stop": False,
            "tool_pause": False,
            "body_len": 500,
        },
        callbacks=callbacks,
        progress=progress,
    )
    assert len(events) == 1
    assert events[0].startswith("content_proof:")


def test_invalid_expected_size_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SubmitProjectAskRequest(prompt_text="x", expected_size="huge")  # type: ignore[arg-type]


def test_invalid_harvest_source_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SubmitProjectAskRequest(prompt_text="x", harvest_source="disk")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_release_f6_clears_pending_and_advances_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production unblock: runner helper clears sticky F6 after archive lands."""
    from cdp_ask.runner import _release_f6_and_advance_proof

    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    req = SubmitProjectAskRequest(
        prompt_text="hello",
        expected_size="large",
        harvest_source="auto",
    )
    execution_id = _EXEC_ID
    archive = Path(default_archive_path(req, execution_id=execution_id))
    _stamped_archive(archive, "x" * 500, execution_id)
    targets = resolve_content_proof_targets(req, execution_id=execution_id)
    events: list[str] = []

    async def on_content_proof(uri: str, sha: str) -> None:
        events.append(f"content_proof:{uri}")

    progress = LadderAdvanceState(
        targets=targets,
        min_bytes=40,
        sha256_file=_sha256_file,
        execution_id=execution_id,
        output_download_pending=True,
        blocked_archive_paths={archive.resolve()},
        turn_idle_sent=True,
    )
    callbacks = LadderCallbacks(on_content_proof=on_content_proof)
    await _release_f6_and_advance_proof(
        progress=progress,
        ladder=callbacks,
        archive_uri=f"cortex://notes/system/threads/{archive.name}",
    )
    assert progress.output_download_pending is False
    assert len(events) == 1
    assert events[0].startswith("content_proof:")


def test_archive_harvest_sha_mismatch_refuses(tmp_path: Path) -> None:
    archive = tmp_path / "notes/system/threads/cdp-ask-archive-new-abcdef12.md"
    _stamped_archive(archive, "x" * 800, _EXEC_ID)
    with pytest.raises(HarvestArchiveError):
        archive_harvest(
            body="y" * 800,
            url="https://claude.ai/new",
            project_uuid="",
            model={"ok": True},
            attested_model="opus-4.8",
            archive_path=str(archive),
            execution_id=_EXEC_ID,
        )
    assert "x" * 800 in archive.read_text(encoding="utf-8")


def test_execution_poll_response_harvest_provenance_field() -> None:
    poll = ExecutionPollResponse(
        execution_id=_EXEC_ID,
        status="completed",
        ok=True,
        harvest_provenance="output-file",
    )
    assert poll.harvest_provenance == "output-file"
    failed = ExecutionPollResponse(
        execution_id=_EXEC_ID,
        status="failed",
        ok=False,
        harvest_provenance=None,
    )
    assert failed.harvest_provenance is None
