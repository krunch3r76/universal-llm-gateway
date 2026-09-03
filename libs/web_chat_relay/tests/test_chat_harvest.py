"""Offline tests for chat_harvest classifier, archive writer, grok fixtures, CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from chat_harvest.archive import (
    ArchiveConflictError,
    ArchiveRefusalError,
    align_transcripts,
    archive_chat_transcript,
    archive_dest,
    build_turn_index,
    parse_index,
    reindex_archive,
    turn_digest,
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
    assert "chat-harvest-index" in body
    assert uri.startswith("cortex://notes/system/threads/chat-harvest-grok-")
    assert len(sha) == 64


def test_writer_extension_reharvest(
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
    assert "chat-harvest-index" in body
    assert uri.endswith(".md")
    assert len(sha) == 64


def test_writer_conflict_on_divergent(
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
    with pytest.raises(ArchiveConflictError) as exc_info:
        archive_chat_transcript("grok", GROK_ID, GROK_URL, changed, harvested_at="t2")
    assert exc_info.value.detail.ordinal == 1
    assert exc_info.value.detail.existing_digest != exc_info.value.detail.new_digest


def test_writer_supersede_moves_existing_to_v2_and_writes_canonical(
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
    canonical = archive_dest("grok", GROK_ID)
    v2 = archive_dest("grok", GROK_ID, version=2)
    assert canonical.is_file()
    assert v2.is_file()
    assert canonical.read_text(encoding="utf-8") != v2.read_text(encoding="utf-8")
    assert "y" in canonical.read_text(encoding="utf-8")
    assert "x" in v2.read_text(encoding="utf-8")
    assert uri == f"cortex://{canonical.relative_to(tmp_path).as_posix()}"


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


def test_index_compare_survives_turn_heading_in_body() -> None:
    body_with_heading = "## Turn 99 — user\nnested heading in body"
    plain = "plain body"
    assert turn_digest(body_with_heading) != turn_digest(plain)
    turns_a = [ChatTurn(author="user", ordinal=1, text=body_with_heading, source="dom")]
    turns_b = [ChatTurn(author="user", ordinal=1, text=body_with_heading, source="dom")]
    assert build_turn_index(turns_a) == build_turn_index(turns_b)


def test_identical_reharvest_does_not_rewrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    turns = [
        ChatTurn(author="user", ordinal=1, text="hello", source="dom"),
        ChatTurn(author="assistant", ordinal=2, text="world", source="dom"),
    ]
    _uri1, sha1 = archive_chat_transcript(
        "grok", GROK_ID, GROK_URL, turns, harvested_at="t1"
    )
    dest = archive_dest("grok", GROK_ID)
    mtime_before = dest.stat().st_mtime
    body_before = dest.read_text(encoding="utf-8")
    _uri2, sha2 = archive_chat_transcript(
        "grok", GROK_ID, GROK_URL, turns, harvested_at="t2"
    )
    assert sha1 == sha2
    assert dest.read_text(encoding="utf-8") == body_before
    assert dest.stat().st_mtime == mtime_before


def test_narrower_capture_does_not_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    full = [
        ChatTurn(author="user", ordinal=1, text="one", source="dom"),
        ChatTurn(author="assistant", ordinal=2, text="two", source="dom"),
        ChatTurn(author="user", ordinal=3, text="three", source="dom"),
    ]
    archive_chat_transcript("grok", GROK_ID, GROK_URL, full, harvested_at="t1")
    partial = full[:2]
    with pytest.raises(ArchiveRefusalError) as exc_info:
        archive_chat_transcript("grok", GROK_ID, GROK_URL, partial, harvested_at="t2")
    assert exc_info.value.code == "narrower_capture"
    body = archive_dest("grok", GROK_ID).read_text(encoding="utf-8")
    assert "## Turn 3 — user" in body


def test_head_extension_refuses_no_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    existing = [
        ChatTurn(author="user", ordinal=3, text="mid", source="dom"),
        ChatTurn(author="assistant", ordinal=4, text="reply", source="dom"),
    ]
    archive_chat_transcript("grok", GROK_ID, GROK_URL, existing, harvested_at="t1")
    extended = [
        ChatTurn(author="user", ordinal=1, text="early", source="dom"),
        ChatTurn(author="assistant", ordinal=2, text="early-reply", source="dom"),
        ChatTurn(author="user", ordinal=3, text="mid", source="dom"),
        ChatTurn(author="assistant", ordinal=4, text="reply", source="dom"),
    ]
    with pytest.raises(ArchiveRefusalError) as exc_info:
        archive_chat_transcript("grok", GROK_ID, GROK_URL, extended, harvested_at="t2")
    assert exc_info.value.code == "head_extension"
    body = archive_dest("grok", GROK_ID).read_text(encoding="utf-8")
    assert "## Turn 3 — user" in body
    assert "early" not in body


def test_window_slide_refuses_with_overlap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    existing = [
        ChatTurn(author="user", ordinal=1, text="t1", source="dom"),
        ChatTurn(author="assistant", ordinal=2, text="t2", source="dom"),
        ChatTurn(author="user", ordinal=3, text="t3", source="dom"),
    ]
    archive_chat_transcript("grok", GROK_ID, GROK_URL, existing, harvested_at="t1")
    slid = [
        ChatTurn(author="assistant", ordinal=1, text="t2", source="dom"),
        ChatTurn(author="user", ordinal=2, text="t3", source="dom"),
        ChatTurn(author="assistant", ordinal=3, text="t4", source="dom"),
    ]
    with pytest.raises(ArchiveRefusalError) as exc_info:
        archive_chat_transcript("grok", GROK_ID, GROK_URL, slid, harvested_at="t2")
    assert exc_info.value.code == "window_slide"
    assert "overlap=1" in exc_info.value.reason


def test_reindex_archive_adds_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    dest = archive_dest("grok", GROK_ID)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "# Chat harvest — grok\n\n"
        "- site: `grok`\n"
        f"- conversation_id: `{GROK_ID}`\n"
        f"- url: `{GROK_URL}`\n"
        "- harvested_at: `t1`\n"
        "- turn_count: `1`\n"
        "- streaming_at_harvest: `false`\n\n"
        "## Turn 1 — user\nlegacy body\n",
        encoding="utf-8",
    )
    result = reindex_archive("grok", GROK_ID)
    assert result is not None
    body = dest.read_text(encoding="utf-8")
    assert parse_index(body) is not None
    assert "chat-harvest-index" in body


def test_reindex_claude_force_rebuilds_stripped_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    cid = "a65cb727-bedf-4c75-bcd8-ae8279ca4b4a"
    dest = archive_dest("claude", cid)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "# Chat harvest — claude\n\n"
        "- site: `claude`\n"
        f"- conversation_id: `{cid}`\n"
        f"- url: `https://claude.ai/chat/{cid}`\n"
        "- harvested_at: `t1`\n"
        "- turn_count: `1`\n"
        "- streaming_at_harvest: `false`\n\n"
        "## Turn 1 — assistant\n"
        "Viewed a file, used toys integration\n\n"
        "Viewed a file, used toys integration\n\n"
        "Yes, two places.\n",
        encoding="utf-8",
    )
    first = reindex_archive("claude", cid)
    assert first is not None
    first_body = dest.read_text(encoding="utf-8")
    first_index = parse_index(first_body)
    assert first_index is not None
    assert first_index[0][2] == turn_digest("Yes, two places.")
    noop = reindex_archive("claude", cid, force=False)
    assert noop == first
    stale = first_body.replace(
        turn_digest("Yes, two places."),
        turn_digest("stale digest"),
        1,
    )
    dest.write_text(stale, encoding="utf-8")
    second = reindex_archive("claude", cid, force=True)
    assert second is not None
    rebuilt_index = parse_index(dest.read_text(encoding="utf-8"))
    assert rebuilt_index is not None
    assert rebuilt_index[0][2] == turn_digest("Yes, two places.")


def test_chrome_doubled_leading_line_stripped() -> None:
    from chat_harvest.claude_chat_adapter import _strip_claude_dom_chrome

    raw = (
        "Viewed a file, used toys integration\n\n"
        "Viewed a file, used toys integration\n\n"
        "Yes, two places."
    )
    assert _strip_claude_dom_chrome(raw) == "Yes, two places."


def test_chrome_doubled_with_icon_glyph_stripped() -> None:
    from chat_harvest.claude_chat_adapter import _strip_claude_dom_chrome

    raw = (
        "Viewed a file, used toys integration\n"
        "\ue02a\n"
        "Viewed a file, used toys integration\n\n"
        "Yes, two places."
    )
    assert _strip_claude_dom_chrome(raw) == "Yes, two places."


def test_chrome_searched_web_icon_glyph_stripped() -> None:
    from chat_harvest.claude_chat_adapter import _strip_claude_dom_chrome

    raw = (
        "Searched the web\n"
        "\ue027\n"
        "Searched the web\n\n"
        "Probably not off the shelf."
    )
    assert _strip_claude_dom_chrome(raw) == "Probably not off the shelf."


def test_chrome_thought_for_duration_stripped() -> None:
    from chat_harvest.claude_chat_adapter import _strip_claude_dom_chrome

    raw = "Thought for 18s\n\nThought for 18s\n\nbody"
    assert _strip_claude_dom_chrome(raw) == "body"


def test_divergent_conflict_reports_first_ordinal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    first = [
        ChatTurn(author="user", ordinal=1, text="alpha", source="dom"),
        ChatTurn(author="assistant", ordinal=2, text="two", source="dom"),
    ]
    archive_chat_transcript("grok", GROK_ID, GROK_URL, first, harvested_at="t1")
    changed = [
        ChatTurn(author="user", ordinal=1, text="alpha", source="dom"),
        ChatTurn(author="assistant", ordinal=2, text="changed", source="dom"),
    ]
    with pytest.raises(ArchiveConflictError) as exc_info:
        archive_chat_transcript("grok", GROK_ID, GROK_URL, changed, harvested_at="t2")
    detail = exc_info.value.detail
    assert detail.ordinal == 2
    assert detail.existing_snippet
    assert detail.new_snippet
    assert detail.existing_digest != detail.new_digest


def test_unindexed_archive_refuses_without_supersede(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    dest = archive_dest("grok", GROK_ID)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "# Chat harvest — grok\n\n## Turn 1 — user\nlegacy without index\n",
        encoding="utf-8",
    )
    turns = [ChatTurn(author="user", ordinal=1, text="legacy without index", source="dom")]
    with pytest.raises(ArchiveRefusalError) as exc_info:
        archive_chat_transcript("grok", GROK_ID, GROK_URL, turns, harvested_at="t2")
    assert exc_info.value.code == "archive_unindexed"


@pytest.mark.asyncio
async def test_conflict_emits_event_with_ordinal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    turns = [ChatTurn(author="user", ordinal=1, text="stored", source="dom")]
    archive_chat_transcript("grok", GROK_ID, GROK_URL, turns, harvested_at="t1")

    emitted: list[dict] = []

    def _capture(event) -> None:  # noqa: ANN001
        emitted.append(dict(event.payload))

    monkeypatch.setattr("cdp_ask.chat_session_harvest.emit", _capture)

    evaluate_result = {
        "login_wall": False,
        "streaming": False,
        "turns": [{"author": "user", "ordinal": 1, "text": "different"}],
    }

    class _FakePw:
        async def stop(self) -> None:
            return None

    class _FakePage:
        async def evaluate(self, _js):  # noqa: ANN001
            return evaluate_result

    async def _fake_attach(**_kwargs):
        return _FakePw(), object(), object(), _FakePage()

    async def _noop_scroll(_page, **_kw) -> None:
        return None

    monkeypatch.setattr(
        "chat_harvest.grok_adapter.grok_session.attach_grok_page",
        _fake_attach,
    )
    monkeypatch.setattr(
        "chat_harvest.grok_adapter.scroll_stabilize",
        _noop_scroll,
    )

    from cdp_ask.chat_session_harvest import execute_harvest
    from cdp_ask.chat_session_models import ChatHarvestRequest

    result = await execute_harvest(
        ChatHarvestRequest(url=GROK_URL, site="grok", cdp_url="http://127.0.0.1:9222")
    )
    assert result.outcome == "archive_conflict"
    assert result.conflict is not None
    assert result.conflict.ordinal == 1
    assert emitted
    assert emitted[0]["conflict_ordinal"] == 1
    assert emitted[0]["outcome"] == "archive_conflict"
    assert "snippet" not in emitted[0]
    assert "existing_digest" not in emitted[0]


def test_conflict_event_carries_no_snippet() -> None:
    """M2 D6 — conversation text must not enter Event Service payloads."""
    from cdp_ask.chat_session_events import mcp_chat_session_harvested

    event = mcp_chat_session_harvested(
        site="grok",
        conversation_id=GROK_ID,
        outcome="archive_conflict",
        turn_count=1,
        conflict_ordinal=1,
        code="archive_conflict",
    )
    payload = dict(event.payload)
    forbidden = {"snippet", "existing_snippet", "new_snippet", "existing_digest", "new_digest"}
    assert forbidden.isdisjoint(payload.keys())
    assert payload.get("conflict_ordinal") == 1


def test_align_transcripts_enum_cases() -> None:
    existing = build_turn_index(
        [ChatTurn(author="user", ordinal=1, text="a", source="dom")]
    )
    identical = [ChatTurn(author="user", ordinal=1, text="a", source="dom")]
    assert align_transcripts(existing, identical).value == "identical"

    extension = identical + [
        ChatTurn(author="assistant", ordinal=2, text="b", source="dom")
    ]
    assert align_transcripts(existing, extension).value == "extension"

    assert align_transcripts(build_turn_index(extension), identical).value == "window"


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


CLAUDE_ID = "thread-uuid"
CLAUDE_URL = f"https://claude.ai/chat/{CLAUDE_ID}"
CSE_URL = "https://claude.ai/cowork/cse_abc123"


class _FakeClaudePage:
    def __init__(
        self,
        *,
        url: str,
        evaluate_result: dict | None = None,
        evaluate_sequence: list[dict] | None = None,
    ) -> None:
        self.url = url
        self._evaluate_result = evaluate_result or {}
        self._evaluate_sequence = list(evaluate_sequence or [])
        self.keyboard = _FakeKeyboard()
        self.closed = False
        self.brought_to_front = False

    async def bring_to_front(self) -> None:
        self.brought_to_front = True
        return None

    async def evaluate(self, _js, _arg=None) -> dict:  # noqa: ANN001
        if self._evaluate_sequence:
            result = dict(self._evaluate_sequence.pop(0))
        else:
            result = dict(self._evaluate_result)
        result.setdefault("url", self.url)
        return result

    async def goto(self, url: str, *, wait_until: str = "") -> None:  # noqa: ARG002
        self.url = url

    async def close(self) -> None:
        self.closed = True

    def get_by_role(self, _role: str, *, name=None):  # noqa: ANN001
        return _FakeLocator(visible=True)


class _FakeKeyboard:
    async def insert_text(self, _text: str) -> None:
        return None


class _FakeLocator:
    def __init__(self, *, visible: bool) -> None:
        self._visible = visible

    async def count(self) -> int:
        return 1

    def nth(self, _index: int) -> _FakeLocator:
        return self

    async def is_visible(self) -> bool:
        return self._visible

    async def click(self, *, force: bool = False) -> None:  # noqa: ARG002
        return None


class _FakeComposer:
    async def click(self, *, force: bool = False) -> None:  # noqa: ARG002
        return None


class _FakeContext:
    def __init__(
        self,
        pages: list[_FakeClaudePage],
        *,
        new_page_result: dict | None = None,
    ) -> None:
        self.pages = pages
        self._new_page_result = new_page_result

    async def new_page(self) -> _FakeClaudePage:
        if self._new_page_result is not None:
            page = _FakeClaudePage(
                url=self._new_page_result.get("url", CLAUDE_URL),
                evaluate_result=self._new_page_result.get("evaluate_result"),
                evaluate_sequence=self._new_page_result.get("evaluate_sequence"),
            )
        else:
            page = _FakeClaudePage(url="https://claude.ai/new")
        self.pages.append(page)
        return page


class _FakePlaywright:
    async def stop(self) -> None:
        return None


@pytest.mark.asyncio
async def test_claude_harvest_cse_url_refuses_without_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail_connect(_cdp_url: str):
        raise AssertionError("connect_cdp must not be called for cse_ URLs")

    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.connect_cdp",
        _fail_connect,
    )
    from chat_harvest.claude_chat_adapter import execute_claude_harvest

    result = await execute_claude_harvest(
        url=CSE_URL,
        site="claude",
        conversation_id="abc123",
        cdp_url="http://127.0.0.1:9222",
    )
    assert result.outcome == "refused"
    assert result.code == "use_cse_session"


def test_claude_turns_from_dom_strip_thinking_prefix() -> None:
    from chat_harvest.claude_chat_adapter import _turns_from_dom
    from claude_bundles.project_ask import strip_thinking_prefix

    raw_turns = [
        {"author": "user", "ordinal": 1, "text": "question"},
        {
            "author": "assistant",
            "ordinal": 2,
            "text": "Thought for 2s\n\nWorked for 3s\n\nanswer",
        },
    ]
    turns = _turns_from_dom(raw_turns)
    assert turns[0].author == "user"
    assert turns[0].ordinal == 1
    assert turns[1].author == "assistant"
    assert turns[1].ordinal == 2
    assert turns[1].text == "Worked for 3s\n\nanswer"


@pytest.mark.asyncio
async def test_claude_harvest_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    evaluate_result = {
        "login_wall": False,
        "streaming": False,
        "turns": [
            {"author": "user", "ordinal": 1, "text": "hello"},
            {"author": "assistant", "ordinal": 2, "text": "hi there"},
        ],
    }
    page = _FakeClaudePage(url=CLAUDE_URL, evaluate_result=evaluate_result)
    context = _FakeContext([page])
    pw = _FakePlaywright()

    async def _fake_connect(_cdp_url: str):
        return pw, object(), context, page

    async def _noop_scroll(_page, **_kwargs) -> None:
        return None

    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.connect_cdp",
        _fake_connect,
    )
    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.scroll_stabilize",
        _noop_scroll,
    )
    from chat_harvest.claude_chat_adapter import execute_claude_harvest

    result = await execute_claude_harvest(
        url=CLAUDE_URL,
        site="claude",
        conversation_id=CLAUDE_ID,
        cdp_url="http://127.0.0.1:9222",
    )
    assert result.outcome == "harvested"
    assert result.conversation_id == CLAUDE_ID
    assert result.archive_uri
    assert result.archive_sha256
    assert result.turn_count == 2


@pytest.mark.asyncio
async def test_claude_harvest_cse_only_tabs_opens_instead_of_refuses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    evaluate_result = {
        "login_wall": False,
        "streaming": False,
        "turns": [
            {"author": "user", "ordinal": 1, "text": "hello"},
            {"author": "assistant", "ordinal": 2, "text": "hi"},
        ],
    }
    cse_page = _FakeClaudePage(url=CSE_URL)
    context = _FakeContext([cse_page], new_page_result={"evaluate_result": evaluate_result})
    pw = _FakePlaywright()

    async def _fake_connect(_cdp_url: str):
        return pw, object(), context, cse_page

    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.connect_cdp",
        _fake_connect,
    )
    from chat_harvest.claude_chat_adapter import execute_claude_harvest

    result = await execute_claude_harvest(
        url=CLAUDE_URL,
        site="claude",
        conversation_id=CLAUDE_ID,
        cdp_url="http://127.0.0.1:9222",
    )
    assert result.outcome == "harvested"
    assert result.opened_on_demand is True
    assert result.turn_count == 2
    minted = [p for p in context.pages if p.url == CLAUDE_URL]
    assert len(minted) == 1
    assert minted[0].closed is True


@pytest.mark.asyncio
async def test_claude_harvest_opens_on_demand_when_no_tab(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    other = _FakeClaudePage(url="https://claude.ai/chat/other-id")
    evaluate_result = {
        "login_wall": False,
        "streaming": False,
        "turns": [
            {"author": "user", "ordinal": 1, "text": "q"},
            {"author": "assistant", "ordinal": 2, "text": "a"},
        ],
    }
    context = _FakeContext([other], new_page_result={"evaluate_result": evaluate_result})
    pw = _FakePlaywright()

    async def _fake_connect(_cdp_url: str):
        return pw, object(), context, other

    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.connect_cdp",
        _fake_connect,
    )
    from chat_harvest.claude_chat_adapter import execute_claude_harvest

    result = await execute_claude_harvest(
        url=CLAUDE_URL,
        site="claude",
        conversation_id=CLAUDE_ID,
        cdp_url="http://127.0.0.1:9222",
    )
    assert result.outcome == "harvested"
    assert result.opened_on_demand is True
    assert result.turn_count == 2
    minted = context.pages[-1]
    assert minted.closed is True


@pytest.mark.asyncio
async def test_claude_harvest_open_on_demand_login_wall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluate_result = {"login_wall": True, "streaming": False, "turns": []}
    context = _FakeContext([], new_page_result={"evaluate_result": evaluate_result})
    pw = _FakePlaywright()

    async def _fake_connect(_cdp_url: str):
        return pw, object(), context, None

    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.connect_cdp",
        _fake_connect,
    )
    from chat_harvest.claude_chat_adapter import execute_claude_harvest

    result = await execute_claude_harvest(
        url=CLAUDE_URL,
        site="claude",
        conversation_id=CLAUDE_ID,
        cdp_url="http://127.0.0.1:9222",
    )
    assert result.outcome == "unauthenticated"
    assert result.opened_on_demand is True
    assert context.pages[-1].closed is True


@pytest.mark.asyncio
async def test_claude_harvest_open_on_demand_incomplete_dom(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    from chat_harvest.archive import archive_dest

    evaluate_result = {"login_wall": False, "streaming": False, "turns": []}
    context = _FakeContext([], new_page_result={"evaluate_result": evaluate_result})
    pw = _FakePlaywright()

    async def _fake_connect(_cdp_url: str):
        return pw, object(), context, None

    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.connect_cdp",
        _fake_connect,
    )

    async def _empty_poll(page, *, max_attempts: int = 20, pause_s: float = 0.25):
        del max_attempts, pause_s
        return await page.evaluate("")

    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter._poll_dom",
        _empty_poll,
    )
    from chat_harvest.claude_chat_adapter import execute_claude_harvest

    result = await execute_claude_harvest(
        url=CLAUDE_URL,
        site="claude",
        conversation_id=CLAUDE_ID,
        cdp_url="http://127.0.0.1:9222",
    )
    assert result.outcome == "unreachable"
    assert result.code == "incomplete_dom"
    assert not archive_dest("claude", CLAUDE_ID).is_file()


@pytest.mark.asyncio
async def test_claude_harvest_open_on_demand_partial_then_full_transcript(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """F8: priming poll may see partial DOM; harvest must use settled full transcript."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    partial = {
        "login_wall": False,
        "streaming": False,
        "turns": [{"author": "user", "ordinal": 1, "text": "one"}],
    }
    full = {
        "login_wall": False,
        "streaming": False,
        "turns": [
            {"author": "user", "ordinal": 1, "text": "one"},
            {"author": "assistant", "ordinal": 2, "text": "two"},
        ],
    }
    context = _FakeContext(
        [],
        new_page_result={"evaluate_sequence": [partial, full]},
    )
    pw = _FakePlaywright()

    async def _fake_connect(_cdp_url: str):
        return pw, object(), context, None

    async def _noop_scroll(_page, **_kwargs) -> None:
        return None

    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.connect_cdp",
        _fake_connect,
    )
    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.scroll_stabilize",
        _noop_scroll,
    )
    from chat_harvest.claude_chat_adapter import execute_claude_harvest

    result = await execute_claude_harvest(
        url=CLAUDE_URL,
        site="claude",
        conversation_id=CLAUDE_ID,
        cdp_url="http://127.0.0.1:9222",
    )
    assert result.outcome == "harvested"
    assert result.opened_on_demand is True
    assert result.turn_count == 2
    assert context.pages[-1].closed is True


@pytest.mark.asyncio
async def test_claude_harvest_existing_tab_not_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    evaluate_result = {
        "login_wall": False,
        "streaming": False,
        "turns": [{"author": "user", "ordinal": 1, "text": "hello"}],
    }
    page = _FakeClaudePage(url=CLAUDE_URL, evaluate_result=evaluate_result)
    context = _FakeContext([page])
    pw = _FakePlaywright()

    async def _fake_connect(_cdp_url: str):
        return pw, object(), context, page

    async def _noop_scroll(_page, **_kwargs) -> None:
        return None

    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.connect_cdp",
        _fake_connect,
    )
    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.scroll_stabilize",
        _noop_scroll,
    )
    from chat_harvest.claude_chat_adapter import execute_claude_harvest

    result = await execute_claude_harvest(
        url=CLAUDE_URL,
        site="claude",
        conversation_id=CLAUDE_ID,
        cdp_url="http://127.0.0.1:9222",
    )
    assert result.outcome == "harvested"
    assert result.opened_on_demand is False
    assert page.closed is False
    assert page.brought_to_front is True


@pytest.mark.asyncio
async def test_claude_probe_opens_on_demand_no_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluate_result = {
        "login_wall": False,
        "streaming": False,
        "turns": [{"author": "assistant", "ordinal": 1, "text": "probe"}],
    }
    context = _FakeContext([], new_page_result={"evaluate_result": evaluate_result})
    pw = _FakePlaywright()

    async def _fake_connect(_cdp_url: str):
        return pw, object(), context, None

    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.connect_cdp",
        _fake_connect,
    )
    from chat_harvest.claude_chat_adapter import execute_claude_harvest

    result = await execute_claude_harvest(
        url=CLAUDE_URL,
        site="claude",
        conversation_id=CLAUDE_ID,
        cdp_url="http://127.0.0.1:9222",
        metadata_only=True,
    )
    assert result.outcome == "harvested"
    assert result.archive_uri is None
    assert result.opened_on_demand is True


@pytest.mark.asyncio
async def test_claude_paste_grant_refuse() -> None:
    from chat_harvest.claude_chat_adapter import execute_claude_paste

    result = await execute_claude_paste(
        url=CLAUDE_URL,
        prompt_text="hello",
        cdp_url="http://127.0.0.1:9222",
        grant="none",
    )
    assert result.ok is False
    assert result.code == "grant_required"


@pytest.mark.asyncio
async def test_claude_paste_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    evaluate_result = {
        "login_wall": False,
        "streaming": False,
        "turns": [
            {"author": "user", "ordinal": 1, "text": "hello"},
            {"author": "assistant", "ordinal": 2, "text": "reply"},
        ],
    }
    page = _FakeClaudePage(url=CLAUDE_URL, evaluate_result=evaluate_result)
    context = _FakeContext([page])
    pw = _FakePlaywright()

    async def _fake_connect(_cdp_url: str):
        return pw, object(), context, page

    async def _fake_harvest_assistant(_page, **_kwargs) -> dict:
        return {"n": 0, "body_len": 0}

    async def _fake_wait(_page, **_kwargs) -> dict:
        return {"n": 1, "body_len": 10}

    async def _fake_find_composer(_page):
        return _FakeComposer()

    async def _noop_scroll(_page, **_kwargs) -> None:
        return None

    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.connect_cdp",
        _fake_connect,
    )
    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.harvest_assistant",
        _fake_harvest_assistant,
    )
    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.wait_assistant_reply",
        _fake_wait,
    )
    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.find_composer",
        _fake_find_composer,
    )
    monkeypatch.setattr(
        "chat_harvest.claude_chat_adapter.scroll_stabilize",
        _noop_scroll,
    )
    from chat_harvest.claude_chat_adapter import execute_claude_paste

    result = await execute_claude_paste(
        url=CLAUDE_URL,
        prompt_text="hello",
        cdp_url="http://127.0.0.1:9222",
        grant="operator",
    )
    assert result.ok is True
    assert result.site == "claude"
    assert result.conversation_id == CLAUDE_ID
    assert result.archive_uri
    assert result.send_verified is True
