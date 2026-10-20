"""14106-like window: memos survive notice noise on resume."""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest
from agent_bus_store import resume_fence_memos
from agent_bus_store.db import create_thread, create_turn, init_db
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.turns import bulk_mark_read_state
from agent_bus_store.house_pools import ContinuityCard
from agent_bus_store.resume_fence import assemble_resume_fence
from agent_bus_store.resume_fence_memos import build_memos_to_check
from agent_bus_store.resume_fence_mission import (
    _bus_tail_turns,
    build_mission_block,
    build_rulings_block,
    house_unread_turns,
)

pytestmark = pytest.mark.offline

_ROOT = "14106"


def _card() -> ContinuityCard:
    text = "## Skills\n- `ulg-for-llms`\n"
    rel = "notes/system/threads/14106-card.md"
    return ContinuityCard(
        status="found",
        tried=(rel,),
        relpath=rel,
        uri=f"cortex://{rel}",
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    init_db()
    yield


def _turn(
    thread: str,
    *,
    sender: str,
    to: str,
    subject: str,
    body: str = "body",
    supersedes_turn: int | None = None,
) -> int:
    _thread, turn_id, _ts, _number, _marked = create_turn(
        thread_id=thread,
        from_agent=sender,
        to_agent=to,
        subject=subject,
        body=body,
        status="open",
        supersedes_turn=supersedes_turn,
    )
    return int(turn_id)


def _noise(thread: str, n: int) -> None:
    patterns = (
        ("cursor", "cursor", "cursor-sdk generate admitted"),
        ("conductor-hop", "conductor-hop", "admitted"),
        ("cursor", "web-anthropic", "LANE CLOSEOUT 9"),
        ("conductor-hop", "cursor", "park-harvest wake"),
        ("closeout-memo", "web-anthropic", "closeout memo — undelivered"),
    )
    for i in range(n):
        sender, to, subject = patterns[i % len(patterns)]
        _turn(thread, sender=sender, to=to, subject=subject, body=f"noise-{i}")


def _seed_house() -> dict[str, int]:
    create_thread(thread_id=_ROOT, slug="house", tags=["role:root"])
    _noise(_ROOT, 30)
    prev_id = _turn(
        _ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="prev cp"
    )
    memo_body = "M" * 900
    _turn(
        _ROOT,
        sender="grok-bot-hm1",
        to="cursor",
        subject="MEMO: please re-run X",
        body=memo_body,
    )
    for i in range(15):
        if i % 2 == 0:
            _turn(
                _ROOT,
                sender="cursor",
                to="cursor",
                subject="cursor-sdk generate admitted",
                body=f"own-{i}",
            )
        else:
            _turn(
                _ROOT,
                sender="conductor-hop",
                to="cursor",
                subject="park-harvest wake",
                body=f"notice-{i}",
            )
    _turn(
        _ROOT,
        sender="cursor",
        to="cursor",
        subject="NOTE successor work",
        body="same-seat note",
    )
    _turn(
        _ROOT,
        sender="grok-bot-ol1",
        to="grok-bot-hm1",
        subject="FACT: not for cursor",
        body="elsewhere",
    )
    tip_id = _turn(
        _ROOT,
        sender="cursor",
        to="cursor",
        subject="CHECKPOINT",
        body="tip cp",
        supersedes_turn=prev_id,
    )
    _turn(
        _ROOT,
        sender="web-anthropic",
        to="cursor",
        subject="MEMO: after tip",
        body="post-tip memo",
    )
    _turn(
        _ROOT,
        sender="grok-bot-hm1",
        to="cursor",
        subject="RULING|keep going",
        body="TYPE: ROUTE/CONTROL\nkind: RULING\n",
    )
    with connect() as conn:
        prev_row = conn.execute(
            "SELECT turn_number FROM turns WHERE id = ?", (prev_id,)
        ).fetchone()
        tip_row = conn.execute(
            "SELECT turn_number FROM turns WHERE id = ?", (tip_id,)
        ).fetchone()
    return {
        "prev_id": prev_id,
        "prev_turn": int(prev_row["turn_number"]),
        "tip_id": tip_id,
        "tip_turn": int(tip_row["turn_number"]),
    }


def test_window_keeps_memos_and_drops_noise(bus_db) -> None:
    ids = _seed_house()
    block = build_memos_to_check(
        _ROOT,
        tip_turn=ids["tip_turn"],
        tip_from_agent="cursor",
        supersedes_turn=ids["prev_id"],
    )
    subjects = [item["subject"] for item in block["items"]]
    assert subjects == [
        "MEMO: please re-run X",
        "NOTE successor work",
        "MEMO: after tip",
        "RULING|keep going",
    ]
    long = block["items"][0]
    assert long["body"] == "M" * 600
    assert long["body_truncated"] is True
    assert long["ref"] == f"agent-bus:{_ROOT}#{long['turn']}"
    assert all(item["turn"] > ids["prev_turn"] for item in block["items"])
    counts = block["counts"]
    assert counts["memos"] == 4
    assert counts["shown"] == 4
    assert counts["truncated"] == 0
    assert counts["dropped_notice"] == 7
    assert counts["dropped_own"] == 8
    assert counts["not_addressed"] == 1
    assert (
        counts["addressed"]
        == counts["memos"] + counts["dropped_notice"] + counts["dropped_own"]
    )
    assert counts["unread_in_window"] == (counts["addressed"] + counts["not_addressed"])
    assert block["window"]["previous_checkpoint_turn"] == ids["prev_turn"]
    assert block["window"]["previous_checkpoint_at"]
    assert block["window"]["tip_turn"] == ids["tip_turn"]
    assert block["window"]["through"] == "now"
    assert block["items"] == sorted(block["items"], key=lambda item: item["turn"])
    assert block["mark_read"] == [
        {
            "tool": "agent_bus",
            "op": "mark_read",
            "thread": _ROOT,
            "agent": "cursor",
            "through_turn": block["items"][-1]["turn"],
        }
    ]


def test_cap_keeps_newest_twelve_memos_not_notices(bus_db) -> None:
    create_thread(thread_id=_ROOT, slug="house", tags=["role:root"])
    _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="p")
    for i in range(20):
        _turn(
            _ROOT,
            sender="grok-bot-hm1",
            to="cursor",
            subject=f"MEMO {i:02d}",
            body=f"memo-{i}",
        )
    for i in range(200):
        _turn(
            _ROOT,
            sender="conductor-hop",
            to="cursor",
            subject="park-harvest wake",
            body=f"n-{i}",
        )
    _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="t")
    with connect() as conn:
        tip_turn = conn.execute(
            "SELECT MAX(turn_number) AS n FROM turns WHERE thread = ?",
            (_ROOT,),
        ).fetchone()["n"]
    block = build_memos_to_check(
        _ROOT,
        tip_turn=int(tip_turn),
        tip_from_agent="cursor",
        supersedes_turn=None,
    )
    assert block["counts"]["shown"] == 12
    assert block["counts"]["memos"] == 20
    assert block["counts"]["truncated"] == 8
    assert block["counts"]["dropped_notice"] == 200
    assert [item["subject"] for item in block["items"]] == [
        f"MEMO {i:02d}" for i in range(8, 20)
    ]
    assert all("wake" not in item["subject"] for item in block["items"])
    root = next(row for row in block["mark_read"] if row["thread"] == _ROOT)
    shown_turns = sorted(int(item["turn"]) for item in block["items"])
    assert root["turn_numbers"] == shown_turns
    assert root.get("partial") is True
    assert root["unshown_memos"] == 8
    assert "through_turn" not in root


def test_partial_mark_read_leaves_unshown_unread(bus_db) -> None:
    create_thread(thread_id=_ROOT, slug="house", tags=["role:root"])
    _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="p")
    memo_turns: list[int] = []
    for i in range(20):
        _turn(
            _ROOT,
            sender="grok-bot-hm1",
            to="cursor",
            subject=f"MEMO {i:02d}",
            body=f"memo-{i}",
        )
        with connect() as conn:
            memo_turns.append(
                int(
                    conn.execute(
                        "SELECT turn_number FROM turns WHERE thread = ? ORDER BY id DESC LIMIT 1",
                        (_ROOT,),
                    ).fetchone()["turn_number"]
                )
            )
    _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="t")
    with connect() as conn:
        tip_turn = int(
            conn.execute(
                "SELECT MAX(turn_number) AS n FROM turns WHERE thread = ?",
                (_ROOT,),
            ).fetchone()["n"]
        )
    block = build_memos_to_check(
        _ROOT,
        tip_turn=tip_turn,
        tip_from_agent="cursor",
        supersedes_turn=None,
    )
    entry = next(row for row in block["mark_read"] if row["thread"] == _ROOT)
    shown_turns = {int(item["turn"]) for item in block["items"]}
    unshown_turns = set(memo_turns) - shown_turns
    bulk_mark_read_state(
        thread=_ROOT,
        turn_numbers=entry["turn_numbers"],
        agent="cursor",
    )
    with connect() as conn:
        for turn in shown_turns:
            row = conn.execute(
                "SELECT read_at FROM turns WHERE thread = ? AND turn_number = ?",
                (_ROOT, turn),
            ).fetchone()
            assert row["read_at"] is not None
        for turn in unshown_turns:
            row = conn.execute(
                "SELECT read_at FROM turns WHERE thread = ? AND turn_number = ?",
                (_ROOT, turn),
            ).fetchone()
            assert row["read_at"] is None


def test_child_lane_partial_mark_read_when_truncated(bus_db) -> None:
    create_thread(thread_id=_ROOT, slug="house", tags=["role:root"])
    create_thread(thread_id="14107", slug="child")
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO thread_lane_associations "
            "(thread_id, parent_thread_id, lane_role) VALUES (?, ?, ?)",
            ("14107", _ROOT, "dispatch"),
        )
    _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="p")
    for i in range(15):
        _turn(
            "14107",
            sender="grok-bot-hm1",
            to="cursor",
            subject=f"MEMO child {i:02d}",
            body=f"child-{i}",
        )
    tip_id = _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="t")
    with connect() as conn:
        tip_turn = int(
            conn.execute(
                "SELECT turn_number FROM turns WHERE id = ?", (tip_id,)
            ).fetchone()["turn_number"]
        )
    block = build_memos_to_check(
        _ROOT,
        tip_turn=tip_turn,
        tip_from_agent="cursor",
        supersedes_turn=None,
    )
    child = next(row for row in block["mark_read"] if row["thread"] == "14107")
    assert child.get("partial") is True
    assert "through_turn" not in child
    assert len(child["turn_numbers"]) == 12
    assert child["unshown_memos"] == 3


def test_child_lane_same_second_as_prev_checkpoint(bus_db) -> None:
    create_thread(thread_id=_ROOT, slug="house", tags=["role:root"])
    create_thread(thread_id="14107", slug="child")
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO thread_lane_associations "
            "(thread_id, parent_thread_id, lane_role) VALUES (?, ?, ?)",
            ("14107", _ROOT, "dispatch"),
        )
    prev_id = _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="p")
    with connect() as conn:
        prev_at = conn.execute(
            "SELECT created_at FROM turns WHERE id = ?", (prev_id,)
        ).fetchone()["created_at"]
    child_id = _turn(
        "14107",
        sender="grok-bot-hm1",
        to="cursor",
        subject="MEMO: child same second",
        body="lane",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at = ? WHERE id = ?",
            (prev_at, child_id),
        )
    tip_id = _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="t")
    with connect() as conn:
        tip_turn = int(
            conn.execute(
                "SELECT turn_number FROM turns WHERE id = ?", (tip_id,)
            ).fetchone()["turn_number"]
        )
    block = build_memos_to_check(
        _ROOT,
        tip_turn=tip_turn,
        tip_from_agent="cursor",
        supersedes_turn=int(prev_id),
    )
    assert [item["ref"] for item in block["items"]] == ["agent-bus:14107#1"]


def test_scan_truncated_exactly_at_limit(
    bus_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    patched_limit = 10
    monkeypatch.setattr(
        resume_fence_memos,
        "_SCAN_LIMIT",
        patched_limit,
    )
    create_thread(thread_id=_ROOT, slug="house", tags=["role:root"])
    _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="p")
    for i in range(patched_limit):
        _turn(
            _ROOT,
            sender="grok-bot-hm1",
            to="cursor",
            subject=f"MEMO {i:03d}",
            body=f"m-{i}",
        )
    _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="t")
    with connect() as conn:
        tip_turn = int(
            conn.execute(
                "SELECT MAX(turn_number) AS n FROM turns WHERE thread = ?",
                (_ROOT,),
            ).fetchone()["n"]
        )
    block = build_memos_to_check(
        _ROOT,
        tip_turn=tip_turn,
        tip_from_agent="cursor",
        supersedes_turn=None,
    )
    assert block["counts"]["scan_truncated"] == 0


def test_scan_truncated_one_over_limit(bus_db, monkeypatch: pytest.MonkeyPatch) -> None:
    patched_limit = 10
    monkeypatch.setattr(
        resume_fence_memos,
        "_SCAN_LIMIT",
        patched_limit,
    )
    create_thread(thread_id=_ROOT, slug="house", tags=["role:root"])
    _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="p")
    for i in range(patched_limit + 1):
        _turn(
            _ROOT,
            sender="grok-bot-hm1",
            to="cursor",
            subject=f"MEMO {i:03d}",
            body=f"m-{i}",
        )
    _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="t")
    with connect() as conn:
        tip_turn = int(
            conn.execute(
                "SELECT MAX(turn_number) AS n FROM turns WHERE thread = ?",
                (_ROOT,),
            ).fetchone()["n"]
        )
    block = build_memos_to_check(
        _ROOT,
        tip_turn=tip_turn,
        tip_from_agent="cursor",
        supersedes_turn=None,
    )
    assert block["counts"]["scan_truncated"] == 1


def test_previous_checkpoint_fallback(bus_db) -> None:
    create_thread(thread_id=_ROOT, slug="house", tags=["role:root"])
    _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="only")
    tip_id = _turn(
        _ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="tip"
    )
    with connect() as conn:
        tip_turn = int(
            conn.execute(
                "SELECT turn_number FROM turns WHERE id = ?", (tip_id,)
            ).fetchone()["turn_number"]
        )
        prev_turn = int(
            conn.execute(
                "SELECT MIN(turn_number) AS n FROM turns WHERE thread = ?",
                (_ROOT,),
            ).fetchone()["n"]
        )
    fallback = build_memos_to_check(
        _ROOT, tip_turn=tip_turn, tip_from_agent="cursor", supersedes_turn=None
    )
    assert fallback["window"]["previous_checkpoint_turn"] == prev_turn
    none = build_memos_to_check(
        _ROOT, tip_turn=1, tip_from_agent="cursor", supersedes_turn=None
    )
    assert none["window"]["previous_checkpoint_turn"] == 0
    assert none["window"]["previous_checkpoint_at"] is None


def test_empty_seat(bus_db) -> None:
    create_thread(thread_id=_ROOT, slug="house", tags=["role:root"])
    _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="t")
    block = build_memos_to_check(
        _ROOT, tip_turn=1, tip_from_agent="", supersedes_turn=None
    )
    assert block["seat"] is None
    assert block["items"] == []
    assert block["counts"]["unread_in_window"] == 0
    assert block["note"] == "no seat mailbox"


def test_child_lane_mark_read(bus_db) -> None:
    create_thread(thread_id=_ROOT, slug="house", tags=["role:root"])
    create_thread(thread_id="14107", slug="child")
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO thread_lane_associations "
            "(thread_id, parent_thread_id, lane_role) VALUES (?, ?, ?)",
            ("14107", _ROOT, "dispatch"),
        )
    prev_id = _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="p")
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00Z", prev_id),
        )
    _turn(
        "14107",
        sender="grok-bot-hm1",
        to="cursor",
        subject="MEMO: child",
        body="on the lane",
    )
    tip_id = _turn(_ROOT, sender="cursor", to="cursor", subject="CHECKPOINT", body="t")
    with connect() as conn:
        tip_turn = int(
            conn.execute(
                "SELECT turn_number FROM turns WHERE id = ?", (tip_id,)
            ).fetchone()["turn_number"]
        )
    block = build_memos_to_check(
        _ROOT,
        tip_turn=tip_turn,
        tip_from_agent="cursor",
        supersedes_turn=int(prev_id),
    )
    assert [item["ref"] for item in block["items"]] == ["agent-bus:14107#1"]
    child = next(row for row in block["mark_read"] if row["thread"] == "14107")
    assert child["agent"] == "cursor"
    assert child["through_turn"] == 1


def test_assemble_is_read_only_and_orders_block(bus_db) -> None:
    ids = _seed_house()
    with connect() as conn:
        before = [
            (int(row["id"]), row["read_at"])
            for row in conn.execute("SELECT id, read_at FROM turns")
        ]
    envelope = {
        "scope": "last_session",
        "seal_status": "sealed",
        "tape_verbal": [],
        "checkpoint_highlight": "h",
        "consolidate_summary_row": "row",
        "summary_row_source": "tip",
        "summary_row_as_of_turn": ids["tip_turn"],
    }
    letter = {"binding": "bound", "body": {"mission": "m", "rulings": []}}
    with (
        patch(
            "agent_bus_store.resume_fence_pour.build_resume_envelope",
            return_value=envelope,
        ),
        patch(
            "agent_bus_store.resume_fence.load_continuity_card",
            return_value=_card(),
        ),
        patch(
            "agent_bus_store.resume_fence_pour.resolve_code_version",
            return_value="abc",
        ),
        patch(
            "agent_bus_store.resume_fence_pour.load_tip_letter",
            return_value=letter,
        ),
    ):
        bundle = assemble_resume_fence(_ROOT, transcript_id="tab", source="test")
    with connect() as conn:
        after = [
            (int(row["id"]), row["read_at"])
            for row in conn.execute("SELECT id, read_at FROM turns")
        ]
    assert after == before
    keys = list(bundle)
    assert keys[keys.index("letter") + 1] == "rulings"
    assert keys[keys.index("rulings") + 1] == "memos_to_check"
    assert bundle["memos_to_check"]["counts"]["shown"] == 4
    steps = bundle["mission"]["handoff"]["protocol_defaults"]["steps"]
    assert any(
        step.startswith("check bundle.memos_to_check first: 4 ") for step in steps
    )
    fold_at = steps.index("fold bus_tail INFO turns")
    memo_at = next(i for i, step in enumerate(steps) if step.startswith("check bundle"))
    assert memo_at < fold_at
    assert bundle["mission"]["memos_to_check"]["shown"] == 4
    assert bundle["mission"]["bus_tail"] == _bus_tail_turns(
        _ROOT, after_turn=ids["prev_turn"]
    )
    assert bundle["mission"]["house_unread"] == house_unread_turns(_ROOT)
    rulings = build_rulings_block(
        _ROOT,
        tip_turn=ids["tip_turn"],
        tip_created_at=bundle["tip_checkpoint"]["created_at"],
        letter=letter,
    )
    assert bundle["rulings"] == rulings
    quiet = build_mission_block(
        thread_id=_ROOT,
        tip_body="tip cp",
        tip_turn=ids["tip_turn"],
        supersedes_turn=ids["prev_id"],
        card_text=None,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf",
        memos={
            "seat": "cursor",
            "counts": {"shown": 0, "memos": 0, "truncated": 0},
            "items": [],
            "window": {"previous_checkpoint_turn": ids["prev_turn"]},
        },
    )
    assert not any("memos_to_check" in step for step in quiet["handoff"]["steps"])


def test_web_anthropic_seat_sees_memo_addressed_to_maestro(bus_db) -> None:
    thread_id = "18469"
    create_thread(thread_id=thread_id, slug="pair-memo-seat")
    prev_id = _turn(
        thread_id,
        sender="cursor",
        to="cursor",
        subject="CHECKPOINT",
        body="prev",
    )
    _turn(
        thread_id,
        sender="cursor",
        to="maestro",
        subject="MEMO: for maestro inbox",
        body="successor",
    )
    tip_id = _turn(
        thread_id,
        sender="web-anthropic",
        to="cursor",
        subject="CHECKPOINT",
        body="tip",
        supersedes_turn=prev_id,
    )
    with connect() as conn:
        prev_row = conn.execute(
            "SELECT turn_number FROM turns WHERE id = ?", (prev_id,)
        ).fetchone()
        tip_row = conn.execute(
            "SELECT turn_number FROM turns WHERE id = ?", (tip_id,)
        ).fetchone()
    block = build_memos_to_check(
        thread_id,
        tip_turn=int(tip_row["turn_number"]),
        tip_from_agent="web-anthropic",
        supersedes_turn=prev_id,
    )
    subjects = [item["subject"] for item in block["items"]]
    assert "MEMO: for maestro inbox" in subjects
    assert block["counts"]["not_addressed"] == 0
