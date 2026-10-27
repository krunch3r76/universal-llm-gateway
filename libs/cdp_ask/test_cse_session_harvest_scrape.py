"""Unit tests for CSE harvest scrape helpers — loading predicate and URL picker."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_bundles.cowork_output_download import HarvestBody

from cdp_ask.cse_session_harvest_scrape import (
    _dom_to_harvested,
    _try_body_harvest,
    compute_incomplete_dom,
    enrich_dom,
    is_loading,
    is_shell_title,
    pick_page_for_chat_url,
)
from cdp_ask.cse_session_models import HarvestRequest

pytestmark = pytest.mark.offline

_OUTPUT_FILENAME = "claude-cowork-070d97ff-5df4-5b11-8181-6faafdf64608.jsonl"
_SESSION_ID = "070d97ff-5df4-5b11-8181-6faafdf64608"
_CHAT_URL = "https://claude.ai/cowork/cse_01AroaPe3tJQSmHTZyVq2XCn"
_REGISTRATION_ID = "37361992cd134cddbbc7fb378dfd492f"
_ROUTE = "classic_affordance:[data-cdp-output-download=1] via _OUTPUT_DOWNLOAD_JS"
_FILE_UUID = "ed1596e0-c550-42af-99e9-1254657e7cb3"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("New task - Claude", True),
        ("New chat - Claude", True),
        ("Claude", True),
        ("", True),
        ("Life coding playbook - Claude", False),
        ("Mid-session task - Claude", False),
    ],
)
def test_is_shell_title(title: str, expected: bool) -> None:
    assert is_shell_title(title) is expected


def test_is_loading_spinner_or_shell() -> None:
    assert is_loading({"spinner": True, "turns": []}) is True
    assert is_loading({"aria_busy": True, "turns": []}) is True
    assert is_loading({"title": "New chat - Claude", "turns": []}) is True
    assert is_loading({"title": "Named session - Claude", "turns": []}) is False


def test_compute_incomplete_dom_requires_loading() -> None:
    assert (
        compute_incomplete_dom({"turns": [], "streaming": False, "loading": True})
        is True
    )
    assert (
        compute_incomplete_dom({"turns": [], "streaming": False, "loading": False})
        is False
    )
    assert (
        compute_incomplete_dom(
            {"turns": [{"text": "x"}], "streaming": False, "loading": True}
        )
        is False
    )
    assert (
        compute_incomplete_dom({"turns": [], "streaming": True, "loading": True})
        is False
    )


def test_enrich_dom_defaults_missing_loading_false() -> None:
    dom = enrich_dom({"turns": [], "streaming": False, "title": "Named - Claude"})
    assert dom["loading"] is False
    assert dom["incomplete_dom"] is False


@pytest.mark.asyncio
async def test_pick_page_for_chat_url_prefers_matching_cse_tab() -> None:
    fallback = MagicMock()
    match = MagicMock()
    match.url = "https://claude.ai/cowork/cse_abc/"
    other = MagicMock()
    other.url = "https://claude.ai/new"
    ctx = MagicMock()
    ctx.pages = [other, match]
    picked = await pick_page_for_chat_url(
        ctx,
        "https://claude.ai/cowork/cse_abc",
        fallback=fallback,
    )
    assert picked is match


def test_dom_to_harvested_maps_coverage_full(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    dom = {
        "turns": [
            {
                "author": "user",
                "text": "User turn with enough text here.",
                "ordinal": 1,
            },
            {
                "author": "assistant",
                "text": "Assistant reply with enough text here.",
                "ordinal": 2,
            },
        ],
        "truncated": False,
        "coverage": "full",
    }
    chat = "https://claude.ai/cowork/cse_archive_test"
    resp = _dom_to_harvested(
        dom,
        HarvestRequest(chat_url=chat),
        None,
        chat_url=chat,
        page_url=chat,
    )
    assert resp.outcome == "harvested"
    assert resp.coverage == "full"
    assert resp.turns[0].author == "user"
    assert resp.turns[0].source == "cse-dom"
    assert resp.archive_uri
    assert resp.archive_sha256
    assert resp.message_count == 2
    assert resp.turn_count == 1


@pytest.mark.asyncio
async def test_pick_page_for_chat_url_falls_back() -> None:
    fallback = MagicMock()
    ctx = MagicMock()
    ctx.pages = [MagicMock(url="https://claude.ai/new")]
    picked = await pick_page_for_chat_url(
        ctx,
        "https://claude.ai/cowork/cse_abc",
        fallback=fallback,
    )
    assert picked is fallback


def _settled_turn_dom(text: str) -> dict[str, object]:
    return {
        "turns": [{"author": "assistant", "text": text, "ordinal": 1}],
        "title": "Session - Claude",
        "streaming": False,
        "in_flight": False,
    }


@pytest.mark.asyncio
async def test_try_body_harvest_persists_output_file_raw_and_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """output-file harvest writes raw bytes + manifest under cortex raw transcripts."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    fixed_date = datetime(2026, 10, 27, 22, 43, 3, tzinfo=UTC)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return fixed_date

    monkeypatch.setattr("cdp_ask.cse_session_harvest_scrape.datetime", _FixedDatetime)

    payload = b'{"type":"user"}\n'
    harvest_body = HarvestBody(
        content=payload.decode("utf-8"),
        provenance="output-file",
        filename=_OUTPUT_FILENAME,
        content_bytes=payload,
        route=_ROUTE,
        file_uuid=_FILE_UUID,
    )
    page = MagicMock()
    req = HarvestRequest(
        source="output-file",
        chat_url=_CHAT_URL,
        registration_id=_REGISTRATION_ID,
    )
    with (
        patch(
            "cdp_ask.cse_session_harvest_scrape.harvest_turns",
            AsyncMock(
                side_effect=[
                    _settled_turn_dom(harvest_body.content),
                    _settled_turn_dom(harvest_body.content),
                ]
            ),
        ),
        patch(
            "cdp_ask.cse_session_harvest_scrape.resolve_harvest_body",
            AsyncMock(return_value=harvest_body),
        ),
    ):
        resp = await _try_body_harvest(page, req, None, chat_url=_CHAT_URL)

    assert resp is not None
    assert resp.outcome == "harvested"
    assert resp.content_provenance == "output-file"
    assert resp.archive_uri
    assert resp.archive_sha256 == hashlib.sha256(payload).hexdigest()

    raw_dir = tmp_path / "notes/system/transcripts/claude-cowork/raw/2026-10-27"
    raw_path = raw_dir / _OUTPUT_FILENAME
    manifest_path = raw_dir / f"{_SESSION_ID}.manifest.json"
    assert raw_path.is_file()
    assert raw_path.read_bytes() == payload
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest) == {
        "sha256",
        "bytes",
        "chat_url",
        "registration_id",
        "sessionId",
        "file_uuid",
        "captured_at",
        "route",
    }
    assert manifest["sha256"] == resp.archive_sha256
    assert manifest["bytes"] == len(payload)
    assert manifest["chat_url"] == _CHAT_URL
    assert manifest["registration_id"] == _REGISTRATION_ID
    assert manifest["sessionId"] == _SESSION_ID
    assert manifest["file_uuid"] == _FILE_UUID
    assert manifest["captured_at"] == "2026-10-27T22:43:03+00:00"
    assert manifest["route"] == _ROUTE
    assert resp.archive_uri == (
        f"cortex://notes/system/transcripts/claude-cowork/raw/2026-10-27/{_OUTPUT_FILENAME}"
    )


@pytest.mark.asyncio
async def test_try_body_harvest_output_file_revision_on_changed_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Changed Output bytes allocate __r2 while preserving the first raw file."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    first = b'{"type":"user"}\n'
    second = b'{"type":"user","rev":2}\n'
    harvest_first = HarvestBody(
        content=first.decode("utf-8"),
        provenance="output-file",
        filename=_OUTPUT_FILENAME,
        content_bytes=first,
        route=_ROUTE,
        file_uuid=_FILE_UUID,
    )
    harvest_second = HarvestBody(
        content=second.decode("utf-8"),
        provenance="output-file",
        filename=_OUTPUT_FILENAME,
        content_bytes=second,
        route=_ROUTE,
        file_uuid=_FILE_UUID,
    )
    page = MagicMock()
    req = HarvestRequest(
        source="output-file",
        chat_url=_CHAT_URL,
        registration_id=_REGISTRATION_ID,
    )

    async def _run(body: HarvestBody):
        with (
            patch(
                "cdp_ask.cse_session_harvest_scrape.harvest_turns",
                AsyncMock(
                    side_effect=[
                        _settled_turn_dom(body.content),
                        _settled_turn_dom(body.content),
                    ]
                ),
            ),
            patch(
                "cdp_ask.cse_session_harvest_scrape.resolve_harvest_body",
                AsyncMock(return_value=body),
            ),
        ):
            return await _try_body_harvest(page, req, None, chat_url=_CHAT_URL)

    first_resp = await _run(harvest_first)
    second_resp = await _run(harvest_second)
    assert first_resp is not None and second_resp is not None

    raw_dir = tmp_path / "notes/system/transcripts/claude-cowork/raw"
    date_dirs = list(raw_dir.iterdir())
    assert len(date_dirs) == 1
    day_dir = date_dirs[0]
    original = day_dir / _OUTPUT_FILENAME
    revised = day_dir / f"claude-cowork-{_SESSION_ID}__r2.jsonl"
    assert original.read_bytes() == first
    assert revised.read_bytes() == second
    assert second_resp.archive_uri.endswith(
        f"claude-cowork-{_SESSION_ID}__r2.jsonl"
    )
    manifest = json.loads(
        (day_dir / f"{_SESSION_ID}__r2.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["supersedes"].endswith(_OUTPUT_FILENAME)
