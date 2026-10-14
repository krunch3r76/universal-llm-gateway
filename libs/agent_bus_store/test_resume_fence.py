"""Resume bundle preamble projection at head and tail."""

from __future__ import annotations

import hashlib

import pytest

from agent_bus_store.db import create_thread, create_turn, init_db
from agent_bus_store.house_pools import ContinuityCard
from agent_bus_store.resume_fence import assemble_resume_fence
from agent_bus_store.resume_fence_mission import (
    build_mission_block,
    extract_tip_preamble,
)

pytestmark = pytest.mark.offline

_PREAMBLE_MD = """# RESUME PREAMBLE — thread 10223
## Stance
House on 10223 is mid-arc.

## Facts
One checkable fact.
These facts are a snapshot taken at checkpoint time and may have drifted.

## Next action
Run the first bounded leg; stop when verified.
"""


def _found_card(text: str) -> ContinuityCard:
    rel = "notes/system/threads/10223-card.md"
    return ContinuityCard(
        status="found",
        tried=(rel,),
        relpath=rel,
        uri=f"cortex://{rel}",
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


@pytest.fixture()
def root_with_sidecar(tmp_path, monkeypatch: pytest.MonkeyPatch):
    files_root = tmp_path / "files"
    sidecar_rel = "notes/system/threads/10223-cp-2-preamble.md"
    sidecar_path = files_root / sidecar_rel
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(_PREAMBLE_MD, encoding="utf-8")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(files_root))
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    init_db()
    create_thread(thread_id="10223", slug="continuity", tags=["role:root"])
    sidecar_uri = f"cortex://{sidecar_rel}"
    tip_with_sidecar = (
        "## Residue (authored — cap ~800 chars)\n"
        "Mission: pointer\n\n## Anchor\n"
        f"Sidecar: {sidecar_uri}\n"
    )
    create_turn(
        thread_id="10223",
        from_agent="cursor",
        to_agent="web",
        subject="CHECKPOINT 10223",
        body=tip_with_sidecar,
        status="open",
    )
    create_turn(
        thread_id="10223",
        from_agent="cursor",
        to_agent="web",
        subject="CHECKPOINT 10223 newer",
        body="## Residue (authored — cap ~800 chars)\nMission: no sidecar\n",
        status="open",
    )
    yield "10223", sidecar_uri


def test_extract_tip_preamble_reads_sidecar_file(root_with_sidecar) -> None:
    _root, uri = root_with_sidecar
    tip_body = (
        "## Residue (authored — cap ~800 chars)\n"
        "Mission: pointer\n\n## Anchor\n"
        f"Sidecar: {uri}\n"
    )
    parsed = extract_tip_preamble(tip_body)
    assert parsed is not None
    assert parsed["stance"] == "House on 10223 is mid-arc."
    assert parsed["next_action"].startswith("Run the first bounded leg")


def test_build_mission_block_includes_preamble_and_next_action(root_with_sidecar) -> None:
    _root, uri = root_with_sidecar
    tip_body = (
        "## Residue (authored — cap ~800 chars)\n"
        "Mission: pointer\n\n## Anchor\n"
        f"Sidecar: {uri}\n"
    )
    mission = build_mission_block(
        thread_id="10223",
        tip_body=tip_body,
        tip_turn=1,
        supersedes_turn=None,
        card_text=None,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-test",
    )
    assert mission["preamble"]["stance"].startswith("House on")
    assert mission["next_action"].startswith("Run the first bounded leg")
    assert any("read mission.preamble" in step for step in mission["handoff"]["steps"])


def test_assemble_resume_fence_preamble_at_head_and_tail(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files_root = tmp_path / "files"
    sidecar_rel = "notes/system/threads/10223-cp-1-preamble.md"
    sidecar_path = files_root / sidecar_rel
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(_PREAMBLE_MD, encoding="utf-8")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(files_root))
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    init_db()
    create_thread(thread_id="10223", slug="continuity", tags=["role:root"])
    sidecar_uri = f"cortex://{sidecar_rel}"
    create_turn(
        thread_id="10223",
        from_agent="cursor",
        to_agent="web",
        subject="CHECKPOINT 10223",
        body=(
            "## Residue (authored — cap ~800 chars)\nMission: p\n\n## Anchor\n"
            f"Sidecar: {sidecar_uri}\n"
        ),
        status="open",
    )
    card = _found_card("## Resume open\nIn one line: test\n")
    monkeypatch.setattr(
        "agent_bus_store.resume_fence.load_continuity_card",
        lambda _thread: card,
    )
    monkeypatch.setattr(
        "agent_bus_store.resume_envelope.build_resume_envelope",
        lambda *a, **k: {"tape_verbal": [], "scope": "last_session"},
    )
    bundle = assemble_resume_fence("10223", transcript_id=None, source="test")
    assert bundle["preamble"]["stance"].startswith("House on")
    assert bundle["tail"]["next_action"].startswith("Run the first bounded leg")


def test_assemble_resume_fence_preamble_none_when_tip_lacks_sidecar(
    root_with_sidecar, monkeypatch: pytest.MonkeyPatch
) -> None:
    card = _found_card("## Resume open\nIn one line: test\n")
    monkeypatch.setattr(
        "agent_bus_store.resume_fence.load_continuity_card",
        lambda _thread: card,
    )
    monkeypatch.setattr(
        "agent_bus_store.resume_envelope.build_resume_envelope",
        lambda *a, **k: {"tape_verbal": [], "scope": "last_session"},
    )
    bundle = assemble_resume_fence("10223", transcript_id=None, source="test")
    assert bundle.get("preamble") is None
    assert bundle.get("tail") is None
