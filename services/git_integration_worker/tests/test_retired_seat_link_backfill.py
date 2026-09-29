"""Retired-seat link backfill: predicate, dry-run, and the stamp stop rule."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from agent_bus_store.db import (
    admit_dispatch,
    create_thread,
    init_db,
    terminate_dispatch,
)
from agent_bus_store.db.connection import write_connect
from agent_bus_store.db.turns import insert_turn

from services.git_integration_worker.cse_session_holders import (
    ensure_schema,
    transition_seat_state,
    upsert_holder,
)
from services.git_integration_worker.cursor_auto.retired_seat_link_backfill import (
    execute_backfill,
)

pytestmark = pytest.mark.offline

_LANE = "12286"
_EXCLUDE = "8dcc0993-8464-46a8-b31c-2e95c8f3c13a"
_DRIVE_REG = "961d9fb1865b49aebed00a0e4b15b241"
_DRIVE_URL = "https://claude.ai/cowork/cse_018K7CSGzGDTaqCioiUS4q5a"
_ORPHANS = (
    (
        "140c033e-6e8f-4072-8ef7-ba1c06ad3d3f",
        "https://claude.ai/cowork/cse_014i5jSfBoYepuvsxswuz1BP",
        "517cdefbc5394176a2018b91e31e9c9f",
        "2026-09-27T17:09:12Z",
        "superseded",
    ),
    (
        "b7cade49-513a-4c83-b5a3-28c0a7657663",
        "https://claude.ai/cowork/cse_017yTyYQH8gBjJze4apyX89o",
        "921e6e4c41184e4b91ea1b9293fd7740",
        "2026-09-27T21:07:10Z",
        "superseded",
    ),
    (
        "68155936-aa4d-4758-a944-50e4d7d969e8",
        "https://claude.ai/cowork/cse_0166v6GC9VAn7bYLwWP9Z84W",
        "71c461830ed34d108376495ba524a484",
        "2026-09-28T06:55:06Z",
        "superseded",
    ),
    (
        "662daf5d-c192-46fa-bec3-066aa4284f1a",
        "https://claude.ai/cowork/cse_01EfichSFhXbmWTCRb7dJjzp",
        "d0f00b85e96242ef84155a2adabb0b82",
        "2026-09-29T07:35:39Z",
        "superseded",
    ),
)
_NULL_CHAT = "88a53ac0-fc39-4d2e-84a9-2e27ae7faf06"
_NULL_BIRTH = "9abb8026293847a486eedb56c65f92e0"
_CHAT_DISPATCH = "a2e67ddc-5d11-4c71-aa4a-cc0350150114"


def _seat(
    conn: sqlite3.Connection,
    *,
    chat_url: str,
    registration_id: str,
    state: str,
) -> None:
    row = upsert_holder(
        conn,
        chat_url=chat_url,
        registration_id=registration_id,
        execution_id=None,
        lane_thread_id=_LANE,
    )
    if state != "driving":
        transition_seat_state(
            conn,
            str(row["holder_id"]),
            to_state=state,
            superseded_by=_DRIVE_REG,
        )


def _link(
    execution_id: str, pipeline_id: str, *, linked_at: str, chat_url: str | None
) -> None:
    admit_dispatch(
        thread_id=_LANE,
        execution_id=execution_id,
        pipeline_id=pipeline_id,
        caller_agent="cursor-auto",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE thread_dispatch_links SET linked_at=?, chat_url=? "
            "WHERE thread_id=? AND execution_id=?",
            (linked_at, chat_url, _LANE, execution_id),
        )


def _registration(body: str, *, created_at: str) -> None:
    insert_turn(
        thread=_LANE,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="TYPE: SEAT_REGISTRATION — successor seated",
        body=body,
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at=? WHERE id = ("
            "SELECT id FROM turns WHERE thread=? ORDER BY turn_number DESC LIMIT 1)",
            (created_at, _LANE),
        )


def _world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    exclude: str = _EXCLUDE,
    extra: tuple[str, str, str, str] | None = None,
    newest_execution: str | None = None,
    newest_birth: str = _DRIVE_REG,
    newest_at: str = "2026-09-29T08:56:18Z",
) -> tuple[Path, Path, Path]:
    """Five known orphans, the live exclude link, and one chat-dispatch row."""
    bus_path = tmp_path / "messages.db"
    ledger_path = tmp_path / "cursor-sdk-dispatch.db"
    watch_path = tmp_path / "hop_cadence_watches.json"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(bus_path))
    init_db()
    create_thread(thread_id=_LANE, slug="lane", lifecycle_state="active")
    _link(
        _CHAT_DISPATCH,
        "chat-dispatch",
        linked_at="2026-09-22T03:39:14Z",
        chat_url=None,
    )
    conn = sqlite3.connect(ledger_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        _seat(conn, chat_url=_DRIVE_URL, registration_id=_DRIVE_REG, state="driving")
        for exec_id, url, reg, _linked, state in _ORPHANS:
            _seat(conn, chat_url=url, registration_id=reg, state=state)
        if extra is not None:
            _exec, url, reg, state = extra
            _seat(conn, chat_url=url, registration_id=reg, state=state)
        conn.commit()
    finally:
        conn.close()
    for exec_id, url, _reg, linked, _state in _ORPHANS:
        _link(exec_id, "cdp-generate", linked_at=linked, chat_url=url)
    _link(
        _NULL_CHAT,
        "cdp-generate",
        linked_at="2026-09-29T01:08:57Z",
        chat_url=None,
    )
    if exclude:
        _link(
            exclude,
            "cdp-generate",
            linked_at="2026-09-29T08:53:24Z",
            chat_url=_DRIVE_URL,
        )
    if extra is not None:
        extra_exec, extra_url, _reg, _state = extra
        _link(
            extra_exec,
            "cdp-generate",
            linked_at="2026-09-29T09:30:00Z",
            chat_url=extra_url,
        )
    _registration(
        "\n".join(
            [
                "TYPE: SEAT_REGISTRATION",
                f"successor_birth_id: {_NULL_BIRTH}",
                f"execution_id={_NULL_CHAT}",
            ]
        ),
        created_at="2026-09-29T01:15:15Z",
    )
    named = _EXCLUDE if newest_execution is None else newest_execution
    _registration(
        "\n".join(
            [
                "TYPE: SEAT_REGISTRATION",
                f"successor_birth_id: {newest_birth}",
                f'birth_record: "execution_id={named}"',
            ]
        ),
        created_at=newest_at,
    )
    watch_path.write_text(
        json.dumps({_LANE: {"thread_id": _LANE, "execution_id": exclude}}),
        encoding="utf-8",
    )
    return watch_path, bus_path, ledger_path


def _run(
    paths: tuple[Path, Path, Path],
    *,
    stamp: bool,
    post: object = None,
) -> list[str]:
    watch, bus_db, ledger = paths
    return execute_backfill(
        lane=_LANE,
        watch_path=watch,
        bus_db=bus_db,
        ledger_db=ledger,
        stamp=stamp,
        post=post,  # type: ignore[arg-type]
    )


def test_dry_run_matches_the_five_and_does_not_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    lines = _run(
        _world(tmp_path, monkeypatch),
        stamp=False,
        post=lambda **kwargs: calls.append(kwargs["execution_id"]) or True,
    )
    assert calls == []
    assert lines[0] == "open_cdp_before=6"
    assert lines[1] == f"exclude={_EXCLUDE}"
    assert lines[2:] == [
        "match=140c033e-6e8f-4072-8ef7-ba1c06ad3d3f",
        "match=b7cade49-513a-4c83-b5a3-28c0a7657663",
        "match=68155936-aa4d-4758-a944-50e4d7d969e8",
        "match=88a53ac0-fc39-4d2e-84a9-2e27ae7faf06",
        "match=662daf5d-c192-46fa-bec3-066aa4284f1a",
    ]
    assert _CHAT_DISPATCH not in "\n".join(lines)
    assert _EXCLUDE not in "\n".join(lines[2:])


def test_stamp_closes_matches_and_second_dry_run_is_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _world(tmp_path, monkeypatch)
    posted: list[str] = []

    def _post(*, lane: str, execution_id: str) -> bool:
        posted.append(execution_id)
        terminate_dispatch(
            thread_id=lane,
            terminal_status="completed",
            execution_id=execution_id,
        )
        return True

    lines = _run(paths, stamp=True, post=_post)
    assert posted == [
        "140c033e-6e8f-4072-8ef7-ba1c06ad3d3f",
        "b7cade49-513a-4c83-b5a3-28c0a7657663",
        "68155936-aa4d-4758-a944-50e4d7d969e8",
        "88a53ac0-fc39-4d2e-84a9-2e27ae7faf06",
        "662daf5d-c192-46fa-bec3-066aa4284f1a",
    ]
    assert "open_cdp_after=1" in lines
    assert f"excluded_still_null={_EXCLUDE}" in lines
    again = _run(paths, stamp=False, post=_post)
    assert not any(line.startswith("match=") for line in again)
    assert again[0] == "open_cdp_before=1"


def test_stamp_refuses_an_id_outside_the_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extra = (
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "https://claude.ai/cowork/cse_01ExtraOutsideAllow",
        "abcabcabcabcabcabcabcabcabcabcab",
        "superseded",
    )
    calls: list[str] = []
    lines = _run(
        _world(tmp_path, monkeypatch, extra=extra),
        stamp=True,
        post=lambda **kwargs: calls.append(kwargs["execution_id"]) or True,
    )
    assert calls == []
    assert "stamp_refused=match_outside_allowlist" in lines
    assert "match=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" in lines
    assert "open_cdp_after=" not in "\n".join(lines)


def test_stamp_allows_a_hop_after_cutoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extra_exec = "cccccccc-dddd-eeee-ffff-000000000001"
    extra = (
        extra_exec,
        "https://claude.ai/cowork/cse_01HopAfterCutoffRet",
        "defdefdefdefdefdefdefdefdefdefde",
        "superseded",
    )
    posted: list[str] = []

    def _post(*, lane: str, execution_id: str) -> bool:
        del lane
        posted.append(execution_id)
        return True

    lines = _run(
        _world(
            tmp_path,
            monkeypatch,
            extra=extra,
            newest_at="2026-09-29T09:20:00Z",
        ),
        stamp=True,
        post=_post,
    )
    assert "stamp_refused=" not in "\n".join(lines)
    assert extra_exec in posted
    assert _EXCLUDE not in posted


def test_stamp_refuses_when_exclude_is_not_the_newest_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = "11111111-2222-3333-4444-555555555555"
    calls: list[str] = []
    lines = _run(
        _world(tmp_path, monkeypatch, newest_execution=other),
        stamp=True,
        post=lambda **kwargs: calls.append(kwargs["execution_id"]) or True,
    )
    assert calls == []
    assert "stamp_refused=exclude_not_newest_seat_registration" in lines
    assert f"exclude={_EXCLUDE}" in lines
    assert f"newest_seat_registration={other}" in lines


def test_stamp_refuses_when_exclude_link_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    lines = _run(
        _world(tmp_path, monkeypatch, exclude=""),
        stamp=True,
        post=lambda **kwargs: calls.append(kwargs["execution_id"]) or True,
    )
    assert calls == []
    assert "stamp_refused=exclude_unverified" in lines
