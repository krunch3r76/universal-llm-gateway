"""Canonical grok-bot transcript store writes on harvest_seal pour."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ._clients import file_sha256
from .conftest import MemFs
from .harvest_adapter import run_harvest_adapter
from .harvest_normalise import run_harvest_normalise
from .harvest_pour import run_harvest_pour
from .result import assemble
from .test_harvest_seal import _page, _put_page
from .transcript_store import persist_grok_checkpoint_transcript

pytestmark = pytest.mark.offline

_AGENT = "68757fe5-d68e-43f6-8708-6abd160bf205"
_FIXTURES = Path(__file__).parent / "fixtures"
_DISPATCH = "21055"


class TranscriptMemFs(MemFs):
    """MemFs plus share-root for canonical transcript persistence."""

    def __init__(self, files_root: Path) -> None:
        super().__init__()
        self.files_root = files_root


@pytest.fixture
def frozen_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return fixed if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(
        "pipelines.grokbot_checkpoint.v1.handlers.transcript_store.datetime",
        _FakeDatetime,
    )
    monkeypatch.setattr("cortex_store.raw_transcript_store.datetime", _FakeDatetime)


def _l0_in_fs(fs: MemFs, *, suffix: str = "2101Z") -> tuple[str, str]:
    raw = (_FIXTURES / "bl1_drift.pages.txt").read_bytes()
    l0 = f"notes/system/tape/captures/grok/{_AGENT}/{suffix}.pages.txt"
    fs.files[l0] = raw
    return l0, file_sha256(raw)


def _base_options(**extra: object) -> dict:
    return {"mode": "harvest_seal", "agent_id": _AGENT, "fallback": "electron", **extra}


async def _pour_chain(
    fs: MemFs,
    options: dict,
) -> dict:
    norm = await run_harvest_normalise(options, fs)
    assert norm.get("refused") is None
    adapter = await run_harvest_adapter(options, norm, fs)
    assert adapter.get("refused") is None
    return await run_harvest_pour(options, adapter, fs, norm)


@pytest.mark.asyncio
async def test_memfs_reports_skipped_and_writes_no_transcripts(
    fs: MemFs, frozen_utc: None
) -> None:
    l0_ref, l0_sha = _l0_in_fs(fs)
    options = _base_options(house="bl1", n=1, l0_ref=l0_ref, l0_sha256=l0_sha)
    pour = await _pour_chain(fs, options)
    assert pour.get("refused") is None
    store = pour.get("transcript_store") or {}
    assert store.get("status") == "skipped"
    assert not any(k.startswith("notes/system/transcripts/") for k in fs.files)


@pytest.mark.asyncio
async def test_houseless_writes_nohouse_raw_and_messages(
    tmp_path: Path, frozen_utc: None
) -> None:
    fs = TranscriptMemFs(tmp_path)
    l0_ref, l0_sha = _l0_in_fs(fs)
    raw_bytes = fs.files[l0_ref]
    options = _base_options(
        houseless=True,
        n=1,
        l0_ref=l0_ref,
        l0_sha256=l0_sha,
        dispatch_thread_id=_DISPATCH,
    )
    pour = await _pour_chain(fs, options)
    assert pour.get("refused") is None
    store = pour["transcript_store"]
    assert store["status"] == "partial"
    assert store["house"] == "_nohouse"
    raw_rel = store["raw_rel_path"]
    msg_rel = store["messages_rel_path"]
    assert raw_rel == (
        f"notes/system/transcripts/_nohouse/grok-bot/raw/"
        f"{raw_rel.split('/')[-2]}/{_AGENT}.txt"
    )
    assert msg_rel.endswith(f"/{_AGENT}.messages.json")
    assert "/grok-bot/messages/" in msg_rel
    assert (tmp_path / raw_rel).read_bytes() == raw_bytes
    raw_manifest = json.loads(
        (tmp_path / store["raw_manifest_rel_path"]).read_text(encoding="utf-8")
    )
    assert raw_manifest["source"] == "grok-bot"
    assert raw_manifest["status"] == "partial"
    assert raw_manifest["known_gap_assertion_id"] == 38940
    assert raw_manifest["sha256"] == file_sha256(raw_bytes)
    msg_manifest = json.loads(
        (tmp_path / store["messages_manifest_rel_path"]).read_text(encoding="utf-8")
    )
    assert msg_manifest["raw_sha256"] == raw_manifest["sha256"]
    assert msg_manifest["raw_rel_path"] == raw_rel
    msg_body = json.loads((tmp_path / msg_rel).read_text())
    assert msg_body["raw_sha256"] == raw_manifest["sha256"]
    assert "grok-agent:" in json.dumps(raw_manifest.get("aliases", []))


@pytest.mark.asyncio
async def test_housed_run_uses_house_dir(tmp_path: Path, frozen_utc: None) -> None:
    fs = TranscriptMemFs(tmp_path)
    l0_ref, l0_sha = _l0_in_fs(fs)
    options = _base_options(house="bl1", n=1, l0_ref=l0_ref, l0_sha256=l0_sha)
    pour = await _pour_chain(fs, options)
    store = pour["transcript_store"]
    assert store["house"] == "bl1"
    assert store["raw_rel_path"].startswith(
        "notes/system/transcripts/bl1/grok-bot/raw/"
    )
    assert store["raw_rel_path"].endswith(f"/{_AGENT}.txt")
    assert f"notes/system/checkpoints/grok/{_AGENT}/1.raw.json" in fs.files


@pytest.mark.asyncio
async def test_longer_n2_writes_r2_with_supersedes(
    tmp_path: Path, frozen_utc: None
) -> None:
    fs = TranscriptMemFs(tmp_path)
    first = _page(0, 0, 2, ["alpha"], start=True)
    second = _page(1, 1, 2, ["beta"], start=False)
    folder = _put_page(fs, 1, 0, 0, first)
    _put_page(fs, 1, 1, 1, second)
    options1 = _base_options(houseless=True, n=1, l0_ref=folder)
    pour1 = await _pour_chain(fs, options1)
    first_raw = pour1["transcript_store"]["raw_rel_path"]
    first_manifest = pour1["transcript_store"]["raw_manifest_rel_path"]
    raw1 = fs.files[f"notes/system/checkpoints/grok/{_AGENT}/1.raw.json"]

    third = _page(2, 2, 3, ["gamma"], start=False)
    folder2 = _put_page(fs, 2, 2, 2, third)
    options2 = {**options1, "n": 2, "l0_ref": folder2, "l0_sha256": ""}
    pour2 = await _pour_chain(fs, options2)
    store2 = pour2["transcript_store"]
    raw2 = fs.files[f"notes/system/checkpoints/grok/{_AGENT}/2.raw.json"]
    assert raw2.startswith(raw1)
    assert store2["raw_rel_path"].endswith(f"{_AGENT}__r2.txt")
    manifest2 = json.loads((tmp_path / store2["raw_manifest_rel_path"]).read_text())
    assert manifest2["supersedes"] == first_manifest
    assert (tmp_path / first_raw).is_file()
    assert (tmp_path / store2["raw_rel_path"]).read_bytes() == raw2


@pytest.mark.asyncio
async def test_identical_rerun_is_idempotent_no_r2(
    tmp_path: Path, frozen_utc: None
) -> None:
    fs = TranscriptMemFs(tmp_path)
    l0_ref, l0_sha = _l0_in_fs(fs)
    raw_bytes = fs.files[l0_ref]
    envelope = {
        "meta": {"provenance": "raw"},
        "messages": [{"role": "user", "content": "x"}],
    }
    options = _base_options(houseless=True, n=1, l0_ref=l0_ref)
    out1 = persist_grok_checkpoint_transcript(
        files_root=tmp_path,
        options=options,
        raw_bytes=raw_bytes,
        raw_envelope=envelope,
        l0_ref=l0_ref,
    )
    out2 = persist_grok_checkpoint_transcript(
        files_root=tmp_path,
        options=options,
        raw_bytes=raw_bytes,
        raw_envelope=envelope,
        l0_ref=l0_ref,
    )
    assert out1["raw_rel_path"] == out2["raw_rel_path"]
    assert "__r2" not in out2["raw_rel_path"]
    date_dir = tmp_path / "notes/system/transcripts/_nohouse/grok-bot/raw"
    r2_glob = list(date_dir.glob(f"*/{_AGENT}__r2*"))
    assert r2_glob == []


@pytest.mark.asyncio
async def test_result_surfaces_transcript_store(
    tmp_path: Path, frozen_utc: None
) -> None:
    fs = TranscriptMemFs(tmp_path)
    l0_ref, l0_sha = _l0_in_fs(fs)
    options = _base_options(house="bl1", n=1, l0_ref=l0_ref, l0_sha256=l0_sha)
    norm = await run_harvest_normalise(options, fs)
    adapter = await run_harvest_adapter(options, norm, fs)
    pour = await run_harvest_pour(options, adapter, fs, norm)
    outputs = {
        "admit": {"houseless": False, "n": 1},
        "harvest_normalise": norm,
        "harvest_adapter": adapter,
        "harvest_pour": pour,
    }
    result = assemble(options, outputs)
    assert result["transcript_store"]["native_id"] == _AGENT
