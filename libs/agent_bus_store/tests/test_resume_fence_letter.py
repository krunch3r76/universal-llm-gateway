"""Resume pour reads the sealed handoff letter before tip_checkpoint (Lane 2b)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_bus_store.db import create_thread, create_turn, init_db
from agent_bus_store.house_pools import ContinuityCard
from agent_bus_store.resume_fence import assemble_resume_fence
from agent_bus_store.resume_fence_letter import load_tip_letter
from agent_bus_store.resume_fence_mission import (
    build_mission_block,
    build_rulings_block,
    mission_marker_preview,
)
from continuity_tape.handoff import HANDOFF_MAX_BYTES, handoff_sha12, stamp_handoff

pytestmark = pytest.mark.offline

_SHA = "ab" * 32
_SID = "cursor-l2b-test"


def _stamped_author(**overrides: object) -> dict:
    letter: dict = {
        "author": "cursor",
        "mission": "ship the letter",
        "where_we_left_off": "seal write",
        "state": "open",
        "next_acts": ["stamp the file"],
        "load_bearing": ["the tip is empty"],
    }
    letter.update(overrides)
    return letter


def _grok_letter(**overrides: object) -> dict:
    letter: dict = {
        "house_cp": "house-cp:h:1",
        "messages_sha256": _SHA,
        "as_of_message_index": 1,
        "author": "grok-bot-hm1",
        "mission": "resume the house",
        "state": "open",
        "next_acts": ["read the card"],
        "load_bearing": ["intent is not in the transcript"],
    }
    letter.update(overrides)
    return letter


def _tip_harvest(
    sid: str = _SID,
    *,
    sha: str = _SHA,
    letter_sha12: str | None = None,
    refused: str | None = None,
    extra_lines: str = "",
) -> str:
    lines = ["## Anchor"]
    if refused:
        lines.append(f"Harvest: refused({refused})")
    else:
        lines.append(
            f"Harvest: transcript:{sid} · messages_sha256:{sha} · "
            "codec:messages-v1 · surface:cursor"
        )
        if letter_sha12 is None:
            lines.append("Letter: handoff:none")
        else:
            lines.append(f"Letter: handoff:{letter_sha12}")
    if extra_lines:
        lines.append(extra_lines)
    return "\n".join(lines) + "\n"


def _write_seal(
    tmp_path,
    sid: str,
    *,
    messages: list | None = None,
    handoff: object = None,
    messages_sha256: str = _SHA,
    session_id: str | None = None,
    corrupt: bool = False,
) -> None:
    path = tmp_path / "notes/system/seals" / f"{sid}.messages.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if corrupt:
        path.write_text("{not-json", encoding="utf-8")
        return
    msgs = messages if messages is not None else [{}]
    payload: dict = {
        "schema": "ulg.continuity.messages/1",
        "messages": msgs,
        "meta": {
            "messages_sha256": messages_sha256,
            "session_id": session_id or sid,
            "transcript_id": "tid",
            "surface": "cursor",
        },
    }
    if handoff is not None:
        payload["handoff"] = handoff
    path.write_text(json.dumps(payload), encoding="utf-8")


def _bound_stamped(
    message_count: int = 1,
    *,
    as_of_turn: int | None = None,
    turns_at_seal: int = 3,
) -> dict:
    author = _stamped_author()
    if as_of_turn is not None:
        author["as_of_turn"] = as_of_turn
    return stamp_handoff(
        author,
        session_id=_SID,
        messages_sha256=_SHA,
        message_count=message_count,
        thread="15902",
        transcript_id="tid",
        surface="cursor",
        turn_count=turns_at_seal,
        now="2026-10-09T18:00:00+00:00",
    )


@pytest.fixture()
def root_thread(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    init_db()
    create_thread(thread_id="15902", slug="continuity", tags=["role:root"])
    yield "15902"


def _envelope_stub() -> dict:
    return {
        "scope": "last_session",
        "seal_status": "sealed",
        "tape_verbal": [],
        "checkpoint_highlight": "highlight",
        "consolidate_summary_row": "row",
        "summary_row_source": "l3",
        "summary_row_as_of_turn": 1,
    }


def _assemble(
    thread_id: str,
    tip_body: str,
    *,
    card_text: str | None = None,
) -> dict:
    card = (
        ContinuityCard(status="missing", tried=())
        if card_text is None
        else ContinuityCard(
            status="found",
            tried=("notes/system/threads/x-card.md",),
            relpath="notes/system/threads/x-card.md",
            uri="cortex://notes/system/threads/x-card.md",
            text=card_text,
            sha256=hashlib.sha256(card_text.encode()).hexdigest(),
        )
    )
    create_turn(
        thread_id=thread_id,
        from_agent="cursor",
        to_agent="cursor",
        subject="CHECKPOINT",
        body=tip_body,
        status="open",
    )
    with (
        patch(
            "agent_bus_store.resume_fence_pour.build_resume_envelope",
            return_value=_envelope_stub(),
        ),
        patch(
            "agent_bus_store.resume_fence.load_continuity_card",
            return_value=card,
        ),
        patch(
            "agent_bus_store.resume_fence_pour.resolve_code_version",
            return_value="abc123",
        ),
    ):
        return assemble_resume_fence(thread_id, transcript_id="tab-x", source="test")


def test_stamped_letter_bound_in_bundle(root_thread, tmp_path) -> None:
    handoff = _bound_stamped(message_count=1)
    _write_seal(tmp_path, _SID, messages=[{}], handoff=handoff)
    sha12 = handoff_sha12(handoff)
    tip = _tip_harvest(letter_sha12=sha12)
    bundle = _assemble(root_thread, tip)
    assert bundle["letter"]["binding"] == "bound"
    assert bundle["letter"]["body"] == handoff
    keys = list(bundle)
    assert keys.index("letter") < keys.index("rulings") < keys.index("tip_checkpoint")
    assert bundle["rulings"]["count"] == 0
    letter_m = bundle["mission"]["letter"]
    assert letter_m["mission"] == "ship the letter"
    handoff = bundle["mission"]["handoff"]
    assert "out_of_scope" not in handoff
    assert "protocol_defaults" in handoff
    steps = handoff["protocol_defaults"]["steps"]
    assert any("predecessor's letter" in step for step in steps)
    preview = mission_marker_preview(bundle["mission"])
    assert preview["letter_binding"] == "bound"


_RULING_MIN = """TYPE: ROUTE/CONTROL
authority: operator
kind: RULING
work_key: assemble-once
"""


def test_assemble_queries_rulings_once(root_thread, tmp_path) -> None:
    handoff = _bound_stamped(message_count=1)
    _write_seal(tmp_path, _SID, messages=[{}], handoff=handoff)
    sha12 = handoff_sha12(handoff)
    tip_body = _tip_harvest(letter_sha12=sha12)
    create_turn(
        thread_id=root_thread,
        from_agent="cursor",
        to_agent="cursor",
        subject="CHECKPOINT",
        body=tip_body,
        status="open",
    )
    create_turn(
        thread_id=root_thread,
        from_agent="cursor",
        to_agent="house",
        subject="RULING|after-a",
        body=_RULING_MIN,
        status="open",
    )
    create_turn(
        thread_id=root_thread,
        from_agent="cursor",
        to_agent="house",
        subject="RULING|after-b",
        body=_RULING_MIN,
        status="open",
    )
    calls = {"n": 0}
    real = build_rulings_block

    def counted(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    card = ContinuityCard(
        status="found",
        tried=("notes/system/threads/x-card.md",),
        relpath="notes/system/threads/x-card.md",
        uri="cortex://notes/system/threads/x-card.md",
        text="## Open\nresume\n",
        sha256=hashlib.sha256(b"card").hexdigest(),
    )
    with (
        patch(
            "agent_bus_store.resume_fence_pour.build_rulings_block",
            side_effect=counted,
        ),
        patch(
            "agent_bus_store.resume_fence_pour.build_resume_envelope",
            return_value=_envelope_stub(),
        ),
        patch(
            "agent_bus_store.resume_fence.load_continuity_card",
            return_value=card,
        ),
        patch(
            "agent_bus_store.resume_fence_pour.resolve_code_version",
            return_value="abc123",
        ),
    ):
        bundle = assemble_resume_fence(
            root_thread, transcript_id="tab-x", source="test"
        )
    assert bundle["mission"]["rulings_after_letter"] == [
        item["ref"] for item in bundle["rulings"]["items"]
    ]
    assert calls["n"] == 1


def test_stale_when_messages_grew(root_thread, tmp_path) -> None:
    handoff = _bound_stamped(message_count=1)
    grown_sha = "cd" * 32
    _write_seal(
        tmp_path,
        _SID,
        messages=[{}, {}],
        handoff=handoff,
        messages_sha256=grown_sha,
    )
    tip = _tip_harvest()
    bundle = _assemble(root_thread, tip)
    assert bundle["letter"]["binding"] == "stale"
    assert "messages_sha256" in bundle["letter"]["reasons"]
    assert "as_of_message_index" in bundle["letter"]["reasons"]
    assert bundle["letter"]["messages_behind"] == 1
    steps = bundle["mission"]["handoff"]["steps"]
    assert any("messages behind the seal" in step for step in steps)


def test_no_handoff_key_pours_with_letter_missing(root_thread, tmp_path) -> None:
    _write_seal(tmp_path, _SID, messages=[{}], handoff=None)
    tip = _tip_harvest()
    bundle = _assemble(root_thread, tip)
    assert bundle["letter"]["binding"] == "missing"
    assert "rulings" not in bundle
    assert bundle["letter"]["reasons"] == ["no_letter"]
    base_keys = {
        "bundle_version",
        "fence",
        "mission",
        "skills_to_use",
        "fence_carriage",
        "tip_checkpoint",
        "resume_envelope",
        "tape",
        "card",
        "projection",
        "opportunities",
        "read_set",
        "stance",
        "provenance",
    }
    assert set(bundle.keys()) == base_keys | {"letter"}


def test_missing_seal_paths(root_thread, tmp_path) -> None:
    refused = load_tip_letter(_tip_harvest(refused="transcript_seal.not_lane_window"))
    assert refused["binding"] == "missing"
    assert refused["reasons"] == ["no_seal"]

    no_harvest = load_tip_letter("## Anchor\nWindow: transcript_id=x\n")
    assert no_harvest["binding"] == "missing"
    assert no_harvest["reasons"] == ["no_seal"]

    _write_seal(tmp_path, _SID, messages=[{}], handoff=_bound_stamped())
    missing_file = load_tip_letter(_tip_harvest(sid="absent-sid"))
    assert missing_file["binding"] == "missing"
    assert missing_file["reasons"] == ["no_file"]

    _write_seal(tmp_path, _SID, corrupt=True)
    unreadable = load_tip_letter(_tip_harvest())
    assert unreadable["binding"] == "missing"
    assert unreadable["reasons"] == ["unreadable"]


def test_non_dict_handoff_stale_shape(root_thread, tmp_path) -> None:
    _write_seal(tmp_path, _SID, messages=[{}], handoff="not-a-dict")
    out = load_tip_letter(_tip_harvest())
    assert out["binding"] == "stale"
    assert out["reasons"] == ["shape"]


def test_grok_profile_bound(root_thread, tmp_path) -> None:
    letter = _grok_letter()
    _write_seal(tmp_path, _SID, messages=[{}], handoff=letter)
    sha12 = handoff_sha12(letter)
    bundle = _assemble(root_thread, _tip_harvest(letter_sha12=sha12))
    assert bundle["letter"]["binding"] == "bound"
    assert bundle["letter"]["body"] == letter


def test_tip_sha12_mismatch_stale(root_thread, tmp_path) -> None:
    handoff = _bound_stamped(message_count=1)
    _write_seal(tmp_path, _SID, messages=[{}], handoff=handoff)
    tip = _tip_harvest(letter_sha12="deadbeef1234")
    bundle = _assemble(root_thread, tip)
    assert bundle["letter"]["binding"] == "stale"
    assert "tip_sha12" in bundle["letter"]["reasons"]


def test_lag_turn_steps(root_thread, tmp_path) -> None:
    handoff = _bound_stamped(message_count=1, as_of_turn=1, turns_at_seal=6)
    _write_seal(tmp_path, _SID, messages=[{}], handoff=handoff)
    tip = _tip_harvest(letter_sha12=handoff_sha12(handoff))
    bundle = _assemble(root_thread, tip)
    steps = bundle["mission"]["handoff"]["protocol_defaults"]["steps"]
    assert any("letter was written 5 turns" in step for step in steps)

    low_lag = {
        "binding": "bound",
        "reasons": [],
        "body": _bound_stamped(message_count=1, as_of_turn=5, turns_at_seal=6),
        "lag_turns": 1,
        "messages_behind": None,
    }
    mission_low = build_mission_block(
        thread_id="15902",
        tip_body=tip,
        tip_turn=1,
        supersedes_turn=None,
        card_text=None,
        envelope=_envelope_stub(),
        pools_row=None,
        open_line=None,
        fence_id="rf-lag",
        letter=low_lag,
    )
    assert not any(
        "letter was written" in step
        for step in mission_low["handoff"]["protocol_defaults"]["steps"]
    )


def test_poured_payload_letter_fields(root_thread, tmp_path) -> None:
    from agent_bus_store.db.connection import connect

    handoff = _bound_stamped(message_count=1)
    _write_seal(tmp_path, _SID, messages=[{}], handoff=handoff)
    sha12 = handoff_sha12(handoff)
    bundle = _assemble(root_thread, _tip_harvest(letter_sha12=sha12))
    with connect() as conn:
        row = conn.execute(
            """
            SELECT payload_json FROM resume_fence_events
            WHERE fence_id = ? AND event = 'poured'
            ORDER BY id DESC LIMIT 1
            """,
            (bundle["fence"]["fence_id"],),
        ).fetchone()
    payload = json.loads(str(row["payload_json"]))
    assert payload["letter_binding"] == "bound"
    assert payload["letter_sha12"] == sha12


@pytest.fixture()
def letter_root(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    return tmp_path


def test_bad_sid_path_traversal_never_reads(letter_root, tmp_path) -> None:
    evil_sid = ".." + "/" + ".." + "/" + "planted"
    planted = letter_root / "planted.messages.json"
    planted.write_text(
        json.dumps(
            {
                "messages": [{}],
                "meta": {"messages_sha256": _SHA, "session_id": evil_sid},
                "handoff": _bound_stamped(message_count=1),
            }
        ),
        encoding="utf-8",
    )
    read_calls: list[Path] = []
    original_read_text = Path.read_text

    def tracking_read_text(self, *args, **kwargs):
        read_calls.append(self)
        return original_read_text(self, *args, **kwargs)

    with patch.object(Path, "read_text", tracking_read_text):
        out = load_tip_letter(_tip_harvest(sid=evil_sid))
    assert out["binding"] == "missing"
    assert out["reasons"] == ["bad_sid"]
    assert read_calls == []


def test_bad_sid_with_slash_rejected(letter_root) -> None:
    sid = "a" + "/" + "b"
    out = load_tip_letter(_tip_harvest(sid=sid))
    assert out["binding"] == "missing"
    assert out["reasons"] == ["bad_sid"]


def test_real_sid_shapes_still_load(letter_root) -> None:
    for sid in (
        "cursor-2026-10-09-064200-f58",
        "grok-bot-2026-10-08-230200-056",
    ):
        author = _stamped_author()
        handoff = stamp_handoff(
            author,
            session_id=sid,
            messages_sha256=_SHA,
            message_count=1,
            thread="15902",
            transcript_id="tid",
            surface="cursor",
            turn_count=3,
            now="2026-10-09T18:00:00+00:00",
        )
        _write_seal(letter_root, sid, messages=[{}], handoff=handoff, session_id=sid)
        out = load_tip_letter(
            _tip_harvest(sid=sid, letter_sha12=handoff_sha12(handoff))
        )
        assert out["binding"] == "bound", sid
        assert out["body"] == handoff


def test_handoff_exceeding_size_cap_strips_body_keeps_binding(letter_root) -> None:
    pad = "x" * (HANDOFF_MAX_BYTES + 64)
    author = _stamped_author(mission=pad)
    handoff = stamp_handoff(
        author,
        session_id=_SID,
        messages_sha256=_SHA,
        message_count=1,
        thread="15902",
        transcript_id="tid",
        surface="cursor",
        turn_count=3,
        now="2026-10-09T18:00:00+00:00",
    )
    _write_seal(letter_root, _SID, messages=[{}], handoff=handoff)
    sha12 = handoff_sha12(handoff)
    out = load_tip_letter(_tip_harvest(letter_sha12=sha12))
    assert out["body"] is None
    assert "too_large" in out["reasons"]
    assert out["binding"] == "bound"


def test_build_mission_letter_after_standing_rules() -> None:
    letter = {
        "binding": "bound",
        "reasons": [],
        "body": _bound_stamped(),
        "lag_turns": None,
        "messages_behind": None,
    }
    mission = build_mission_block(
        thread_id="1",
        tip_body="tip",
        tip_turn=1,
        supersedes_turn=None,
        card_text="## Rules\n- one rule\n",
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-x",
        letter=letter,
    )
    steps = mission["handoff"]["protocol_defaults"]["steps"]
    assert steps[0].startswith("Load mission.standing_rules")
    assert "predecessor's letter" in steps[1]
