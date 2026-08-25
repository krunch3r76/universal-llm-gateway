"""Offline tests for chat_harvest classifier, archive writer, grok fixtures, CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from chat_harvest.archive import (
    ArchiveConflictError,
    archive_chat_transcript,
    archive_dest,
    is_prefix_superset,
    parse_turns_from_archive,
)
from chat_harvest.models import (
    ChatTurn,
    ClassifyRefuse,
    classify_chat_url,
    project_turns_view,
    relay_lock_fresh,
)
from web_chat_relay import cli
from web_chat_relay.grok_session import strip_chrome

pytestmark = pytest.mark.offline

GROK_ID = "47794c69-9fcc-4481-b1a6-f6c9cbf8b768"
GROK_URL = f"https://grok.com/c/{GROK_ID}"


@pytest.mark.parametrize(
    ("url", "site", "expected_site", "expected_id", "code"),
    [
        (GROK_URL, None, "grok", GROK_ID, None),
        ("https://www.grok.com/c/abc-123", None, "grok", "abc-123", None),
        ("https://grok.com/", None, "grok", "", None),
        ("https://grok.com/settings", None, None, None, "unknown_grok_path"),
        ("https://claude.ai/chat/thread-uuid", None, "claude", "thread-uuid", None),
        ("https://claude.ai/new", None, "claude", "", None),
        ("https://claude.ai/new?foo=1", None, "claude", "", None),
        (
            "https://claude.ai/cowork/cse_abc123",
            None,
            None,
            None,
            "use_cse_session",
        ),
        (
            "https://claude.ai/Cowork/CSE_abc123",
            None,
            None,
            None,
            "use_cse_session",
        ),
        ("https://example.com/chat/x", None, None, None, "unsupported_site"),
        (GROK_URL, "claude", None, None, "site_mismatch"),
        ("", None, None, None, "url_required"),
    ],
)
def test_classify_chat_url_table(
    url: str,
    site: str | None,
    expected_site: str | None,
    expected_id: str | None,
    code: str | None,
) -> None:
    result = classify_chat_url(url, site=site)
    if code is not None:
        assert isinstance(result, ClassifyRefuse)
        assert result.code == code
        return
    assert result.ok is True
    assert result.site == expected_site
    assert result.conversation_id == expected_id


def test_writer_first_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    turns = [
        ChatTurn(author="user", ordinal=1, text="hello", source="dom"),
        ChatTurn(author="assistant", ordinal=2, text="hi", source="dom"),
    ]
    uri, sha = archive_chat_transcript(
        "grok",
        GROK_ID,
        GROK_URL,
        turns,
        harvested_at="2026-08-25T12:00:00+00:00",
        streaming=False,
    )
    dest = archive_dest("grok", GROK_ID)
    assert dest.is_file()
    body = dest.read_text(encoding="utf-8")
    assert "## Turn 1 — user" in body
    assert "## Turn 2 — assistant" in body
    assert uri.startswith("cortex://notes/system/threads/chat-harvest-grok-")
    assert len(sha) == 64


def test_writer_prefix_superset_reharvest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    first = [
        ChatTurn(author="user", ordinal=1, text="one", source="dom"),
        ChatTurn(author="assistant", ordinal=2, text="two", source="dom"),
    ]
    archive_chat_transcript("grok", GROK_ID, GROK_URL, first, harvested_at="t1")
    extended = first + [
        ChatTurn(author="user", ordinal=3, text="three", source="dom"),
    ]
    uri, sha = archive_chat_transcript(
        "grok", GROK_ID, GROK_URL, extended, harvested_at="t2"
    )
    body = archive_dest("grok", GROK_ID).read_text(encoding="utf-8")
    assert "## Turn 3 — user" in body
    assert uri.endswith(".md")
    assert len(sha) == 64


def test_writer_conflict_on_non_superset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    first = [
        ChatTurn(author="user", ordinal=1, text="alpha", source="dom"),
    ]
    archive_chat_transcript("grok", GROK_ID, GROK_URL, first, harvested_at="t1")
    changed = [
        ChatTurn(author="user", ordinal=1, text="beta", source="dom"),
    ]
    with pytest.raises(ArchiveConflictError):
        archive_chat_transcript("grok", GROK_ID, GROK_URL, changed, harvested_at="t2")


def test_writer_supersede_writes_v2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    turns = [ChatTurn(author="user", ordinal=1, text="x", source="dom")]
    archive_chat_transcript("grok", GROK_ID, GROK_URL, turns, harvested_at="t1")
    uri, _sha = archive_chat_transcript(
        "grok",
        GROK_ID,
        GROK_URL,
        [ChatTurn(author="user", ordinal=1, text="y", source="dom")],
        harvested_at="t2",
        supersede=True,
    )
    assert "-v2.md" in uri
    assert archive_dest("grok", GROK_ID, version=2).is_file()


def test_writer_empty_id_does_not_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="non-empty"):
        archive_chat_transcript("grok", "", "https://grok.com/", [], harvested_at="t")


def test_truncated_view_does_not_cap_stored_turns() -> None:
    turns = [
        ChatTurn(author="user", ordinal=i, text=f"t{i}", source="dom")
        for i in range(1, 21)
    ]
    view, truncated = project_turns_view(turns, include_turns="range", limit=5)
    assert len(view) == 5
    assert truncated is True
    assert len(turns) == 20


def test_grok_fixture_user_assistant_ordinals() -> None:
    raw_turns = [
        {"author": "user", "ordinal": 1, "text": "question"},
        {"author": "assistant", "ordinal": 2, "text": "Worked for 3s\n\nanswer"},
    ]
    from chat_harvest.grok_adapter import _turns_from_dom

    turns = _turns_from_dom(raw_turns)
    assert turns[0].author == "user"
    assert turns[0].ordinal == 1
    assert turns[1].author == "assistant"
    assert turns[1].ordinal == 2
    assert turns[1].text == strip_chrome("Worked for 3s\n\nanswer")


def test_prefix_superset_helper() -> None:
    existing = [(1, "user", "a"), (2, "assistant", "b")]
    assert is_prefix_superset(existing, existing)
    assert is_prefix_superset(existing, existing + [(3, "user", "c")])
    assert not is_prefix_superset(existing, [(1, "user", "x")])


def test_parse_turns_from_archive() -> None:
    content = """# Chat harvest — grok

## Turn 1 — user
hello

## Turn 2 — assistant
world
"""
    parsed = parse_turns_from_archive(content)
    assert parsed == [(1, "user", "hello"), (2, "assistant", "world")]


def test_cli_harvest_json_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_harvest(**_kwargs):
        from chat_harvest.models import ChatHarvestResponse

        return ChatHarvestResponse(
            outcome="harvested",
            site="grok",
            conversation_id=GROK_ID,
            url=GROK_URL,
            archive_uri="cortex://notes/system/threads/x.md",
            archive_sha256="abc",
            turn_count=2,
        )

    monkeypatch.setattr(cli, "execute_grok_harvest", fake_harvest)
    rc = cli.main(["--harvest", "--grok-url", GROK_URL])
    assert rc == 0


def test_cli_harvest_json_lacks_last_assistant(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_harvest(**_kwargs):
        from chat_harvest.models import ChatHarvestResponse

        return ChatHarvestResponse(
            outcome="harvested",
            site="grok",
            conversation_id=GROK_ID,
            url=GROK_URL,
            archive_uri="cortex://notes/system/threads/x.md",
            archive_sha256="abc",
            turn_count=2,
        )

    monkeypatch.setattr(cli, "execute_grok_harvest", fake_harvest)
    cli.main(["--harvest", "--grok-url", GROK_URL])
    payload = json.loads(capsys.readouterr().out)
    assert payload["archive_uri"]
    assert payload["archive_sha256"]
    assert "last_assistant" not in payload


def test_cli_no_out_index_status_flags() -> None:
    parser = cli._parser()
    flags = {a.dest for a in parser._actions if a.dest != "help"}
    assert "out" not in flags
    assert "index" not in flags
    assert "status" not in flags


def test_relay_lock_fresh(tmp_path: Path) -> None:
    import time

    state = tmp_path / "state.json"
    assert not relay_lock_fresh(state)
    state.write_text(
        json.dumps({"updated_at": time.time() - 200}),
        encoding="utf-8",
    )
    assert not relay_lock_fresh(state)
    state.write_text(json.dumps({"updated_at": time.time() - 5}), encoding="utf-8")
    assert relay_lock_fresh(state)
