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
    assert turns[1].text == strip_thinking_prefix(raw_turns[1]["text"])


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
