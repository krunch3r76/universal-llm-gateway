"""Unit tests for CHECKPOINT Windows render-on-read."""

from __future__ import annotations

import pytest
from agent_bus_store.checkpoint_projection import RESUME_FOOTER_PREFIX
from agent_bus_store.checkpoint_windows_render import (
    CheckpointTurnRow,
    WindowRow,
    extract_arc_from_summary,
    inject_windows_section,
    join_windows,
    journal_cites_thread,
    maybe_render_checkpoint_windows,
    render_windows_section,
    render_windows_table,
    should_render_windows,
)

pytestmark = pytest.mark.offline


def test_extract_arc_from_summary_line_and_prefix() -> None:
    assert extract_arc_from_summary("Arc: shipped G3 windows render") == "shipped G3 windows render"
    assert extract_arc_from_summary("Lead line\nArc: nested arc") == "nested arc"
    assert extract_arc_from_summary("No arc here") is None


def test_journal_cites_thread_variants() -> None:
    assert journal_cites_thread(entity_ids=["agent-bus:6341"], thread_id="6341")
    assert journal_cites_thread(entity_ids=["6341"], thread_id="6341")
    assert journal_cites_thread(entity_ids=["agent-bus:6341#turn-3"], thread_id="6341")
    assert not journal_cites_thread(entity_ids=["agent-bus:9999"], thread_id="6341")


def test_join_windows_pairs_journal_to_cp_interval() -> None:
    turns = (
        CheckpointTurnRow(3, 1, "2026-09-01T10:00:00", "CHECKPOINT v1"),
        CheckpointTurnRow(7, 2, "2026-09-01T12:00:00", "CHECKPOINT v2"),
    )
    journals = (
        {
            "id": 10,
            "timestamp": "2026-09-01T11:00:00",
            "summary": "Arc: first window closed",
            "session_id": "cursor-2026-09-01-1000-abc",
            "entity_ids": ["agent-bus:6341"],
        },
        {
            "id": 11,
            "timestamp": "2026-09-01T13:00:00",
            "summary": "Arc: second window closed",
            "session_id": "cursor-2026-09-01-1200-def",
            "entity_ids": ["6341"],
        },
    )
    rows = join_windows(checkpoint_turns=turns, journals=journals)
    assert len(rows) == 2
    assert rows[0] == WindowRow(
        cp_ordinal=1,
        turn=3,
        session_id="cursor-2026-09-01-1000-abc",
        arc="first window closed",
        journal_row_id=10,
    )
    assert rows[1] == WindowRow(
        cp_ordinal=2,
        turn=7,
        session_id="cursor-2026-09-01-1200-def",
        arc="second window closed",
        journal_row_id=11,
    )


def test_join_windows_open_cp_without_journal() -> None:
    turns = (CheckpointTurnRow(1, 1, "2026-09-01T10:00:00", "CHECKPOINT birth"),)
    rows = join_windows(checkpoint_turns=turns, journals=())
    assert rows == (
        WindowRow(
            cp_ordinal=1,
            turn=1,
            session_id=None,
            arc=None,
            journal_row_id=None,
        ),
    )


def test_render_windows_table_and_section() -> None:
    rows = (
        WindowRow(1, 3, "sess-a", "arc text", 42),
        WindowRow(2, 5, None, None, None),
    )
    table = render_windows_table(rows)
    assert "| 1 | 3 | sess-a | arc text | 42 |" in table
    assert "| 2 | 5 |  |  |  |" in table
    section = render_windows_section(rows=rows)
    assert "## Windows (rendered at read" in section
    assert table in section


def test_inject_windows_before_residue() -> None:
    body = (
        "## Derived (projected at post — do not hand-edit)\n"
        "### Child lanes\n_none_\n\n"
        "## Residue (authored — cap ~800 chars)\nWIP\n"
        f"{RESUME_FOOTER_PREFIX} load checkpoint-discipline"
    )
    windows = "## Windows (rendered at read — do not hand-edit)\n| cp_ordinal | turn |"
    out = inject_windows_section(body, windows)
    assert out.index("## Derived") < out.index(windows) < out.index("## Residue")
    assert RESUME_FOOTER_PREFIX in out


def test_should_render_windows_gates_on_root_and_subject() -> None:
    assert should_render_windows(subject="CHECKPOINT v1", thread_tags=["role:root"])
    assert not should_render_windows(subject="WIP", thread_tags=["role:root"])
    assert not should_render_windows(subject="CHECKPOINT v1", thread_tags=[])


def test_maybe_render_skips_non_root() -> None:
    body = "## Residue\nhello"
    out = maybe_render_checkpoint_windows(
        thread="6341",
        subject="CHECKPOINT v1",
        body=body,
        thread_tags=[],
    )
    assert out == body


def test_maybe_render_injects_for_root_with_mocked_fetch() -> None:
    turns = (CheckpointTurnRow(2, 1, "2026-09-01T10:00:00", "CHECKPOINT v1"),)

    def _fetch(*, thread_id: str) -> tuple[dict, ...]:
        assert thread_id == "9861"
        return (
            {
                "id": 99,
                "timestamp": "2026-09-01T10:30:00",
                "summary": "Arc: render-on-read verified",
                "session_id": "cursor-2026-09-01-1030-xyz",
                "entity_ids": ["agent-bus:9861"],
            },
        )

    body = "## Residue (authored — cap ~800 chars)\nWIP"
    out = maybe_render_checkpoint_windows(
        thread="9861",
        subject="CHECKPOINT v1",
        body=body,
        thread_tags=["role:root"],
        checkpoint_turns=turns,
        journal_fetcher=_fetch,
    )
    assert "## Windows (rendered at read" in out
    assert "| 1 | 2 | cursor-2026-09-01-1030-xyz | render-on-read verified | 99 |" in out


def test_maybe_render_fail_open_banner_on_journal_error() -> None:
    turns = (CheckpointTurnRow(1, 1, "2026-09-01T10:00:00", "CHECKPOINT birth"),)

    def _boom(*, thread_id: str) -> tuple[dict, ...]:
        raise RuntimeError("cortex down")

    body = "## Residue\nbirth"
    out = maybe_render_checkpoint_windows(
        thread="9861",
        subject="CHECKPOINT birth",
        body=body,
        thread_tags=["role:root"],
        checkpoint_turns=turns,
        journal_fetcher=_boom,
    )
    assert "UNRENDERED" in out
    assert "| 1 | 1 |  |  |  |" in out


def test_read_route_renders_windows(tmp_path, monkeypatch) -> None:
    from agent_bus_store import create_app
    from agent_bus_store.auth import require_token
    from fastapi.testclient import TestClient

    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None

    turns = (CheckpointTurnRow(1, 1, "2026-09-01T10:00:00", "CHECKPOINT birth"),)

    def _fetch(*, thread_id: str) -> tuple[dict, ...]:
        return (
            {
                "id": 7,
                "timestamp": "2026-09-01T10:05:00",
                "summary": "Arc: route join",
                "session_id": "cursor-route",
                "entity_ids": [f"agent-bus:{thread_id}"],
            },
        )

    monkeypatch.setattr(
        "agent_bus_store.checkpoint_windows_render.list_checkpoint_turns",
        lambda *, thread_id: turns,
    )
    monkeypatch.setattr(
        "agent_bus_store.checkpoint_windows_render.fetch_journals_for_thread",
        _fetch,
    )

    with TestClient(app) as client:
        created = client.post(
            "/threads/with-turn",
            json={
                "slug": "win-read",
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT birth",
                "body": "## Residue (authored — cap ~800 chars)\nWIP",
                "tags": ["role:root"],
            },
        )
        assert created.status_code == 201, created.text
        thread_id = created.json()["thread"]["id"]
        resp = client.get(
            "/turns/by-number",
            params={"thread": thread_id, "turn_number": "1"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["body"]
        assert "## Windows (rendered at read" in body
        assert "| 1 | 1 | cursor-route | route join | 7 |" in body
