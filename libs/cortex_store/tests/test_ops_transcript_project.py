"""Offline tests for ops_transcript_project (P6-P7, P9, AC-12)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_store.dispatch_ops.ops_transcript_project import run_transcript_project
from cortex_store.transcript_projection_membership import detect_anchor_mismatches
from cortex_store.transcript_projection_facts import BusSendFact, WindowFacts


def _bus_fixture() -> dict[str, object]:
    turns_10223 = [
        {
            "turn_number": 155,
            "subject": "CHECKPOINT — CP155",
            "body": "Window: transcript_id=9c37637d-379f-467e-89fd-849f6950ee19 · turns@cp=6",
        },
        {
            "turn_number": 157,
            "subject": "CHECKPOINT — CP157",
            "body": "Window: transcript_id=9c37637d-379f-467e-89fd-849f6950ee19 · turns@cp=87",
        },
    ]

    def bus_get(path: str):
        if path == "/threads/10223":
            return {"id": "10223", "tags": ["spine=root"], "created_at": "2026-01-01T00:00:00Z"}
        if path == "/threads/10223/lineage":
            return {"children": [{"thread_id": "10303"}]}
        if path == "/turns?thread=10223":
            return turns_10223
        if path == "/turns?thread=10303":
            return []
        return None

    return {"get": bus_get}


@pytest.mark.offline
def test_p7_anchor_mismatch_uuid_count() -> None:
    wf = WindowFacts(transcript_id="c6f9360d-6bc7-4245-8304-478455356dad", turn_count=14)
    wf.bus_sends = [
        BusSendFact(
            thread="10223",
            kind="CHECKPOINT",
            subject="CHECKPOINT — CP157",
            subject_head="CHECKPOINT — CP157",
            turn_index=14,
            record_index=0,
        )
    ]
    rows = detect_anchor_mismatches(
        "10223",
        bus_get=_bus_fixture()["get"],
        member_facts={wf.transcript_id: wf},
    )
    cp157 = [r for r in rows if r.cp_turn == 157]
    assert len(cp157) == 1
    assert cp157[0].kind == "uuid+count"


@pytest.mark.offline
def test_p9_refusals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "files"))
    (tmp_path / "files" / "notes" / "system" / "threads").mkdir(parents=True)
    out = run_transcript_project(thread="", bus_get=_bus_fixture()["get"])
    assert "missing_thread" in out["error"]
    out2 = run_transcript_project(thread="99999", bus_get=lambda p: None)
    assert "thread_not_found" in out2["error"]


@pytest.mark.offline
def test_p9_dry_run_no_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files_root = tmp_path / "files"
    transcripts = tmp_path / "agent-transcripts"
    tid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    jsonl = transcripts / tid / f"{tid}.jsonl"
    jsonl.parent.mkdir(parents=True)
    jsonl.write_text(
        json.dumps(
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": "<user_query>hi</user_query>"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(files_root))
    out = run_transcript_project(
        thread="10223",
        dry_run=True,
        bus_get=_bus_fixture()["get"],
        transcripts_root=transcripts,
        transcript_ids=[tid],
    )
    assert "open_line" in out
    assert list(out.keys())[0] == "open_line"
    assert not (files_root / "notes/system/threads/10223-transcript-projection.state.json").exists()


@pytest.mark.offline
def test_p10_surface_enum_in_soc() -> None:
    from pathlib import Path
    import sys

    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo / "services" / "mcp-server"))
    from _derive import _CORTEX_CENSUS_SIZE, derive_cortex_surface

    assert _CORTEX_CENSUS_SIZE == 74
    code = derive_cortex_surface("code", repo / "config/mcp/canonical.yaml")
    life = derive_cortex_surface("life", repo / "config/mcp/canonical.yaml")
    assert "transcript_project" in code.ops_enum
    assert "transcript_project" not in life.ops_enum
