"""Offline tests for CSE DOM harvest persistence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from chat_harvest.archive import archive_dest, archive_rel_path
from chat_harvest.cse_persist import (
    CSE_HARVEST_NO_LIMIT,
    persist_cse_dom_harvest,
    resolve_cse_harvest_limit,
)
from chat_harvest.models import ChatTurn

pytestmark = pytest.mark.offline

CSE_URL = "https://claude.ai/cowork/cse_01Q2V5ex1bc9DqatVpyFeJgY"
CSE_ID = "cse_01Q2V5ex1bc9DqatVpyFeJgY"


def test_resolve_cse_harvest_limit_default_is_unlimited() -> None:
    assert resolve_cse_harvest_limit(None) == CSE_HARVEST_NO_LIMIT
    assert resolve_cse_harvest_limit(25) == 25


def test_persist_cse_dom_harvest_specimen_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    dom_turns = []
    ordinal = 0
    for turn in range(7):
        ordinal += 1
        dom_turns.append(
            {
                "author": "user",
                "text": f"User turn {turn + 1} with enough text.",
                "ordinal": ordinal,
            }
        )
        ordinal += 1
        dom_turns.append(
            {
                "author": "assistant",
                "text": f"Assistant reply {turn + 1} with enough text.",
                "ordinal": ordinal,
            }
        )
    uri, sha, alignment, turn_count, message_count = persist_cse_dom_harvest(
        chat_url=CSE_URL,
        page_url=CSE_URL,
        dom_turns=dom_turns,
    )
    expected_rel = archive_rel_path("claude", CSE_ID)
    assert uri == f"cortex://{expected_rel}"
    assert alignment == "first_write"
    assert turn_count == 7
    assert message_count == 14
    dest = archive_dest("claude", CSE_ID)
    raw = dest.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == sha
    payload = json.loads(raw.decode("utf-8"))
    assert payload["schema"] == "ulg.continuity.messages/1"
    assert len(payload["messages"]) == 14


def test_persist_identical_reharvest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    rows = [
        {"author": "user", "text": "Question one with enough text.", "ordinal": 1},
        {
            "author": "assistant",
            "text": "Answer one with enough text here.",
            "ordinal": 2,
        },
    ]
    _uri, sha1, alignment1, _, _ = persist_cse_dom_harvest(
        chat_url=CSE_URL,
        page_url=CSE_URL,
        dom_turns=rows,
    )
    uri2, sha2, alignment2, _, _ = persist_cse_dom_harvest(
        chat_url=CSE_URL,
        page_url=CSE_URL,
        dom_turns=rows,
    )
    assert alignment1 == "first_write"
    assert alignment2 == "identical"
    assert sha1 == sha2
    assert uri2.endswith(".messages.json")


def test_persist_extension_reharvest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    first = [
        ChatTurn(author="user", ordinal=1, text="one", source="dom"),
        ChatTurn(author="assistant", ordinal=2, text="two", source="dom"),
    ]
    from chat_harvest.archive import archive_chat_transcript

    archive_chat_transcript("claude", CSE_ID, CSE_URL, first, harvested_at="t1")
    extended_rows = first + [
        ChatTurn(author="user", ordinal=3, text="three", source="dom"),
    ]
    dom = [
        {"author": t.author, "text": t.text, "ordinal": t.ordinal}
        for t in extended_rows
    ]
    _uri, _sha, alignment, turn_count, message_count = persist_cse_dom_harvest(
        chat_url=CSE_URL,
        page_url=CSE_URL,
        dom_turns=dom,
    )
    assert alignment == "extension"
    assert turn_count == 2
    assert message_count == 3
