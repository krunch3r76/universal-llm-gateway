"""Tests for resume bundle mission block (FIX-16..18)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest
from agent_bus_store.db import create_thread, create_turn, init_db
from agent_bus_store.resume_fence_mission import (
    _CLAIMS_BUDGET_CHARS,
    build_mission_block,
    build_standing_rules,
    lookup_assertions,
    mission_marker_preview,
    sketchboard_uri,
)

pytestmark = pytest.mark.offline

_TIP = """
## Residue (authored — cap ~800 chars)
## Anchor
Window: transcript_id=d556c84f-524e-4e57-b38c-6fdc3eafe8fb · turns@cp=22

## Highlight
Structural resume PASS.

## Settled
FIX-7..14 landed.

## Next
FIX-18 shrink mission block.

Sketchboard: cortex://notes/system/threads/10223-resume-fence-sketchboard.md
"""


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    init_db()
    create_thread(thread_id="10223", slug="continuity", tags=["role:root"])
    create_turn(
        thread_id="10223",
        from_agent="cursor",
        to_agent="house",
        subject="INFO relay",
        body="TYPE: INFO",
        status="open",
    )
    create_turn(
        thread_id="10223",
        from_agent="cursor",
        to_agent="house",
        subject="CHECKPOINT",
        body=_TIP,
        status="open",
    )
    yield


def test_sketchboard_uri_from_tip() -> None:
    uri = sketchboard_uri("10223", _TIP)
    assert uri.endswith("10223-resume-fence-sketchboard.md")


def test_build_mission_block_includes_residue_and_window(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=None,
        envelope={
            "checkpoint_highlight": "Structural resume PASS.",
            "consolidate_summary_row": "row",
            "summary_row_source": "tip_checkpoint_residue",
            "summary_row_as_of_turn": 2,
        },
        pools_row=None,
        open_line=None,
        fence_id="rf-test1234",
    )
    assert mission["fence_id"] == "rf-test1234"
    assert mission["window_anchor"]["transcript_id"].startswith("d556c84f")
    assert mission["window_anchor"].keys() == {"transcript_id", "turns_at_cp"}
    assert mission["lifecycle"]["clone_mode"] == "B"
    assert not any("scope=window" in step for step in mission["handoff"]["steps"])
    assert mission["lifecycle"]["release"] == "pour_terminal"
    assert "FIX-18" in (mission.get("residue") or "")
    assert mission["handoff"]["todo"] == "todo:continuity-resume-fence"
    assert mission["bus_tail"] == ["10223#1"]
    assert mission["house_unread"] == [
        {
            "turn": 1,
            "from": "cursor",
            "to": "house",
            "subject": "INFO relay",
            "body": "TYPE: INFO",
        }
    ]
    assert any("house_unread" in step for step in mission["handoff"]["steps"])


def test_handoff_steps_thread_slug_and_out_of_scope(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body="plain tip without sketchboard uri",
        tip_turn=2,
        supersedes_turn=None,
        card_text=None,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-slug",
        thread_slug="liaison-autonomous-night",
    )
    steps = mission["handoff"]["steps"]
    assert "rename_chat → `10223 liaison-autonomous-night`" in steps
    assert not any("read sketchboard" in step for step in steps)
    assert mission["handoff"]["out_of_scope"] == [
        "parallel WIP outside this root's scoreboard",
        "new implement until oriented",
    ]


def test_handoff_sketchboard_step_only_when_named_in_tip(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=None,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-sketch",
        thread_slug="continuity",
    )
    steps = mission["handoff"]["steps"]
    assert any(
        "read sketchboard cortex://notes/system/threads/10223-resume-fence-sketchboard.md"
        in step
        for step in steps
    )


def test_handoff_omits_rename_when_slug_missing(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=None,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-noslug",
        thread_slug=None,
    )
    assert not any("rename_chat" in step for step in mission["handoff"]["steps"])


def test_house_unread_includes_same_from_note(bus_db) -> None:
    create_turn(
        thread_id="10223",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="NOTE a:38216 specimen",
        body="TYPE: NOTE\nfrom: 15266",
        status="open",
    )
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=None,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-note",
    )
    notes = [row for row in mission["house_unread"] if row["turn"] == 3]
    assert notes == [
        {
            "turn": 3,
            "from": "cursor",
            "to": "web-anthropic",
            "subject": "NOTE a:38216 specimen",
            "body": "TYPE: NOTE\nfrom: 15266",
        }
    ]


def test_mission_marker_preview_is_subset(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=None,
        envelope={"checkpoint_highlight": "hi"},
        pools_row=None,
        open_line="one line",
        fence_id="rf-abcd",
    )
    preview = mission_marker_preview(mission)
    assert preview["highlight"] == "hi"
    assert preview["clone_mode"] == "B"
    assert preview["skills_to_use"] == []
    assert "handoff" not in preview
    assert "residue" not in preview


_SKILLS_CARD = """
## Skills
- `outbound-voice-spec`
- `prose-discipline`

## Stance
Use the `ulg-for-llms` skill.
"""


def test_mission_skills_to_use_from_card_and_step_order(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=_SKILLS_CARD,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-skills",
        thread_slug="continuity",
    )
    assert mission["skills_to_use"] == [
        {
            "slug": "outbound-voice-spec",
            "use_line": "Use the `outbound-voice-spec` skill",
        },
        {
            "slug": "prose-discipline",
            "use_line": "Use the `prose-discipline` skill",
        },
    ]
    steps = mission["handoff"]["steps"]
    assert steps[0] == "continuity(op=resume) was first hop"
    assert steps[1] == (
        "Use each mission.skills_to_use slug now — before orientation "
        "and before any act the slug governs"
    )
    assert "rename_chat → `10223 continuity`" in steps
    preview = mission_marker_preview(mission)
    assert preview["skills_to_use"] == mission["skills_to_use"]
    assert preview["standing_rules_count"] == 0
    assert preview["standing_rules_first"] is None


def test_standing_rules_resolve_assertions(
    bus_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_claim = "no rice " * 80

    def _fake(assertion_ids: list[int]) -> dict:
        out = {}
        for assertion_id in assertion_ids:
            if assertion_id == 31294:
                out[assertion_id] = {
                    "id": "a:31294",
                    "status": "current",
                    "claim": long_claim,
                    "claim_truncated": False,
                }
            elif assertion_id == 1:
                out[assertion_id] = {
                    "id": "a:1",
                    "status": "superseded",
                    "claim": "old claim",
                    "claim_truncated": False,
                }
            elif assertion_id == 2:
                out[assertion_id] = {
                    "id": "a:2",
                    "status": "elapsed",
                    "claim": "withdrawn",
                    "claim_truncated": False,
                }
            else:
                out[assertion_id] = {
                    "id": f"a:{assertion_id}",
                    "status": "unresolved",
                    "claim": "",
                    "claim_truncated": False,
                }
        return out

    monkeypatch.setattr(
        "agent_bus_store.resume_fence_mission.lookup_assertions",
        _fake,
    )
    row = (
        "- Diet, loaded before any meal: `person:kaywan-mansubi` "
        "a:31294 · a:1 · a:2. No rice."
    )
    card = f"## Rules\n{row}\n\n## Skills\n- `ulg-for-llms`\n"
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=card,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-rules",
    )
    assert mission["standing_rules"][0]["text"] == row
    by_id = {item["id"]: item for item in mission["standing_rules"][0]["assertions"]}
    assert by_id["a:31294"]["status"] == "current"
    assert by_id["a:31294"]["claim_truncated"] is True
    assert len(by_id["a:31294"]["claim"]) <= 240
    assert by_id["a:1"]["status"] == "superseded"
    assert by_id["a:2"]["status"] == "elapsed"
    steps = mission["handoff"]["steps"]
    assert steps[0].startswith("Load mission.standing_rules")
    assert steps[1] == "continuity(op=resume) was first hop"


def test_missing_rules_section_degrades(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text="## Stance\nUse the `ulg-for-llms` skill.\n",
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-norules",
    )
    assert mission["standing_rules"] == []
    assert not any("standing_rules" in step for step in mission["handoff"]["steps"])


def test_missing_card_degrades(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=None,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-nocard",
    )
    assert mission["standing_rules"] == []


def test_mission_no_skills_section_empty_list_no_step(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text="## Stance\nUse the `ulg-for-llms` skill.\n",
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-noskills",
    )
    assert mission["skills_to_use"] == []
    assert not any("skills_to_use" in step for step in mission["handoff"]["steps"])


def _assertion_db(rows: list[tuple]) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE assertions ("
        "id INTEGER PRIMARY KEY, claim TEXT, superseded_by INTEGER, "
        "review_status TEXT, valid_until TEXT)"
    )
    db.executemany(
        "INSERT INTO assertions (id, claim, superseded_by, review_status, valid_until) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    return db


class _ConnWrap:
    def __init__(self, db: sqlite3.Connection, opens: list[object]) -> None:
        self._db = db
        self.statements: list[str] = []
        opens.append(self)

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        self.statements.append(sql)
        return self._db.execute(sql, params)

    def close(self) -> None:
        return None


def test_supersede_chain_cycle_and_elapsed(monkeypatch: pytest.MonkeyPatch) -> None:
    past_z = "2026-10-08T12:00:01Z"
    past_offset = "2026-10-08T12:00:01+00:00"
    db = _assertion_db(
        [
            (1, "old", 2, "committed", None),
            (2, "mid", 3, "committed", None),
            (3, "live claim", None, "committed", None),
            (10, "cycle-a", 11, "committed", None),
            (11, "cycle-b", 10, "committed", None),
            (20, "ended-z", None, "committed", past_z),
            (22, "ended-offset", None, "committed", past_offset),
            (21, "no", None, "rejected", None),
            (30, "cited-reject", 31, "committed", None),
            (31, "rejected tip", None, "rejected", None),
            (40, "cited-elapsed", 41, "committed", None),
            (41, "elapsed tip", None, "committed", past_z),
            (50, "bad stamp", None, "committed", "not-a-time"),
        ]
    )
    opens: list[_ConnWrap] = []

    def _conn(**_kwargs: object) -> _ConnWrap:
        return _ConnWrap(db, opens)

    monkeypatch.setattr("cortex_store.db.cortex_conn", _conn)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return datetime(2026, 10, 8, 12, 0, 1, 500000, tzinfo=UTC)

    monkeypatch.setattr("agent_bus_store.resume_fence_mission.datetime", _Frozen)
    found = lookup_assertions([1, 10, 20, 22, 21, 30, 40, 50, 99])
    assert found[1]["status"] == "superseded"
    assert found[1]["current_status"] == "current"
    assert found[1]["current_id"] == "a:3"
    assert found[1]["id"] == "a:1"
    assert found[1]["claim"] == "live claim"
    assert found[10]["status"] == "superseded"
    assert found[10]["current_status"] == "current"
    assert found[10]["current_id"] == "a:11"
    assert found[10]["claim"] == "cycle-b"
    assert found[20]["status"] == "elapsed"
    assert found[22]["status"] == "elapsed"
    assert found[21]["status"] == "rejected"
    assert found[30]["status"] == "superseded"
    assert found[30]["current_status"] == "rejected"
    assert found[30]["current_id"] == "a:31"
    assert found[30]["claim"] == "cited-reject"
    assert found[40]["status"] == "superseded"
    assert found[40]["current_status"] == "elapsed"
    assert found[40]["claim"] == "cited-elapsed"
    assert found[50]["status"] == "current"
    assert found[50]["valid_until_note"] == "unparseable"
    assert found[99]["status"] == "unresolved"
    assert len(opens) == 1


def test_dangling_supersede_mid_chain_and_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _assertion_db(
        [
            (1, "cited-dangling", 99, "committed", None),
            (2, "cited-mid", 3, "committed", None),
            (3, "mid-row", 98, "committed", None),
            (4, "healthy-cited", 5, "committed", None),
            (5, "healthy-tip", None, "committed", None),
        ]
    )
    opens: list[_ConnWrap] = []
    monkeypatch.setattr(
        "cortex_store.db.cortex_conn",
        lambda **_kwargs: _ConnWrap(db, opens),
    )
    found = lookup_assertions([1, 2, 4])
    assert found[1]["status"] == "superseded"
    assert found[1]["current_status"] == "unresolved"
    assert found[1]["dangling_successor"] == "a:99"
    assert "current_id" not in found[1]
    assert found[1]["claim"] == "cited-dangling"
    assert found[2]["status"] == "superseded"
    assert found[2]["current_status"] == "unresolved"
    assert found[2]["dangling_successor"] == "a:98"
    assert found[2]["current_id"] == "a:3"
    assert found[2]["claim"] == "cited-mid"
    assert found[4]["status"] == "superseded"
    assert found[4]["current_status"] == "current"
    assert found[4]["current_id"] == "a:5"
    assert found[4]["claim"] == "healthy-tip"
    assert "dangling_successor" not in found[4]

    rules = build_standing_rules("## Rules\n- cite a:1 and a:2\n")
    by_id = {item["id"]: item for item in rules[0]["assertions"]}
    assert by_id["a:1"]["dangling_successor"] == "a:99"
    assert by_id["a:1"]["current_status"] == "unresolved"
    assert "current_id" not in by_id["a:1"]
    assert by_id["a:2"]["dangling_successor"] == "a:98"
    assert by_id["a:2"]["current_id"] == "a:3"
    assert by_id["a:2"]["current_status"] == "unresolved"


def test_lookup_missing_db_is_lookup_error_and_creates_nothing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "no-such-cortex.db"
    monkeypatch.setattr("cortex_store.db._CORTEX_DB", missing)
    found = lookup_assertions([1])
    assert found[1]["status"] == "lookup_error"
    assert found[1]["claim"] == ""
    assert not missing.exists()
    assert not missing.with_name(missing.name + "-wal").exists()


def test_lookup_error_is_not_unresolved(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom(**_kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("store down")

    monkeypatch.setattr("cortex_store.db.cortex_conn", _boom)
    with caplog.at_level("ERROR"):
        found = lookup_assertions([7])
    assert found[7]["status"] == "lookup_error"
    assert "assertion lookup failed" in caplog.text


def test_many_ids_keep_rows_under_claim_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    claim = "x" * 500
    db = _assertion_db([(i, claim, None, "committed", None) for i in range(1, 101)])
    opens: list[_ConnWrap] = []
    monkeypatch.setattr(
        "cortex_store.db.cortex_conn",
        lambda **_kwargs: _ConnWrap(db, opens),
    )
    lines = [
        f"- rule {n}: " + " ".join(f"a:{n * 5 + k}" for k in range(1, 6))
        for n in range(20)
    ]
    card = "## Rules\n" + "\n".join(lines) + "\n"
    rules = build_standing_rules(card)
    assert len(rules) == 20
    assert [row["text"] for row in rules] == lines
    total = sum(len(item["claim"]) for row in rules for item in row["assertions"])
    assert total <= _CLAIMS_BUDGET_CHARS
    assert len(opens) == 1
    assert sum(1 for wrap in opens for sql in wrap.statements if " IN " in sql) >= 1
