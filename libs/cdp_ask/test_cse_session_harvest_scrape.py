"""Unit tests for CSE harvest scrape helpers — loading predicate and URL picker."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_bundles.cowork_output_download import HarvestBody, OutputDownloadError

from cdp_ask.cse_session_harvest_scrape import (
    _dom_to_harvested,
    _try_body_harvest,
    compute_incomplete_dom,
    enrich_dom,
    harvest_with_loading_wait,
    is_loading,
    is_shell_title,
    pick_page_for_chat_url,
)
from cdp_ask.cse_session_models import HarvestRequest

pytestmark = pytest.mark.offline

_OUTPUT_FILENAME_COWORK = "claude-cowork-070d97ff-5df4-5b11-8181-6faafdf64608.jsonl"
_OUTPUT_FILENAME_CLAUDE_AI = "claude-ai-070d97ff-5df4-5b11-8181-6faafdf64608.jsonl"
_RAW_BASENAME = "070d97ff-5df4-5b11-8181-6faafdf64608.jsonl"
_SESSION_ID = "070d97ff-5df4-5b11-8181-6faafdf64608"
_MANIFEST_KEYS = frozenset(
    {
        "sha256",
        "bytes",
        "captured_at",
        "house",
        "source",
        "kind",
        "native_id",
        "sessionId",
        "chat_url",
        "registration_id",
        "file_uuid",
        "route",
        "supersedes",
    }
)
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


def _freeze_harvest_date(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_date = datetime(2026, 10, 27, 22, 43, 3, tzinfo=UTC)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return fixed_date

    monkeypatch.setattr("cdp_ask.cse_session_harvest_scrape.datetime", _FixedDatetime)


@pytest.mark.parametrize(
    ("download_filename", "provenance", "house"),
    [
        (_OUTPUT_FILENAME_COWORK, {"lane_thread_proven": "12286"}, "12286"),
        (_OUTPUT_FILENAME_CLAUDE_AI, None, "_nohouse"),
    ],
)
@pytest.mark.asyncio
async def test_try_body_harvest_persists_output_file_raw_and_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    download_filename: str,
    provenance: dict[str, str] | None,
    house: str,
) -> None:
    """output-file harvest writes ``<house>/claude-ai/raw/<date>/<native_id>.jsonl``."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _freeze_harvest_date(monkeypatch)

    payload = b'{"type":"user"}\n'
    harvest_body = HarvestBody(
        content=payload.decode("utf-8"),
        provenance="output-file",
        filename=download_filename,
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
    harvest_turns = AsyncMock(
        return_value=_settled_turn_dom(harvest_body.content),
    )
    with (
        patch("cdp_ask.cse_session_harvest_scrape.harvest_turns", harvest_turns),
        patch(
            "cdp_ask.cse_session_harvest_scrape.resolve_harvest_body",
            AsyncMock(return_value=harvest_body),
        ),
    ):
        resp = await _try_body_harvest(
            page, req, provenance, chat_url=_CHAT_URL
        )

    assert resp is not None
    assert resp.outcome == "harvested"
    assert resp.content_provenance == "output-file"
    assert resp.archive_sha256 == hashlib.sha256(payload).hexdigest()

    raw_dir = (
        tmp_path / "notes/system/transcripts" / house / "claude-ai/raw/2026-10-27"
    )
    raw_path = raw_dir / _RAW_BASENAME
    manifest_path = raw_dir / f"{_SESSION_ID}.manifest.json"
    assert raw_path.is_file()
    assert raw_path.read_bytes() == payload
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest) == _MANIFEST_KEYS
    assert manifest["house"] == house
    assert manifest["source"] == "claude-ai"
    assert manifest["kind"] == "raw"
    assert manifest["native_id"] == _SESSION_ID
    assert manifest["sessionId"] == _SESSION_ID
    assert manifest["supersedes"] is None
    assert resp.archive_uri == (
        "cortex://notes/system/transcripts/"
        f"{house}/claude-ai/raw/2026-10-27/{_RAW_BASENAME}"
    )


@pytest.mark.asyncio
async def test_try_body_harvest_output_file_revision_on_changed_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Changed Output bytes allocate ``__r2`` while preserving the first raw file."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _freeze_harvest_date(monkeypatch)
    first = b'{"type":"user"}\n'
    second = b'{"type":"user","rev":2}\n'
    harvest_first = HarvestBody(
        content=first.decode("utf-8"),
        provenance="output-file",
        filename=_OUTPUT_FILENAME_COWORK,
        content_bytes=first,
        route=_ROUTE,
        file_uuid=_FILE_UUID,
    )
    harvest_second = HarvestBody(
        content=second.decode("utf-8"),
        provenance="output-file",
        filename=_OUTPUT_FILENAME_COWORK,
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
                AsyncMock(return_value=_settled_turn_dom(body.content)),
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

    day_dir = tmp_path / "notes/system/transcripts/_nohouse/claude-ai/raw/2026-10-27"
    original = day_dir / _RAW_BASENAME
    revised = day_dir / f"{_SESSION_ID}__r2.jsonl"
    assert original.read_bytes() == first
    assert revised.read_bytes() == second
    assert second_resp.archive_uri.endswith(f"{_SESSION_ID}__r2.jsonl")
    manifest = json.loads(
        (day_dir / f"{_SESSION_ID}__r2.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["supersedes"] == (
        "notes/system/transcripts/_nohouse/claude-ai/raw/2026-10-27/"
        f"{_SESSION_ID}.manifest.json"
    )


def _output_harvest_body(content: bytes) -> HarvestBody:
    return HarvestBody(
        content=content.decode("utf-8"),
        provenance="output-file",
        filename=_OUTPUT_FILENAME_COWORK,
        content_bytes=content,
        route=_ROUTE,
        file_uuid=_FILE_UUID,
    )


@pytest.mark.asyncio
async def test_output_file_loading_wait_succeeds_on_third_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cold Outputs panel: miss twice in the wait loop, harvest on the third attempt."""
    monkeypatch.setattr(
        "cdp_ask.cse_session_harvest_scrape.OPEN_ON_DEMAND_INCOMPLETE_WAIT_S",
        5.0,
    )
    payload = b'{"type":"user"}\n' * 40
    body = _output_harvest_body(payload)
    page = MagicMock()
    req = HarvestRequest(source="output-file", chat_url=_CHAT_URL)

    for run in range(3):
        resolve_mock = AsyncMock(
            side_effect=[
                OutputDownloadError("cold panel"),
                OutputDownloadError("still cold"),
                body,
            ]
        )
        with (
            patch(
                "cdp_ask.cse_session_harvest_scrape.harvest_turns",
                AsyncMock(return_value=_settled_turn_dom(body.content)),
            ),
            patch(
                "cdp_ask.cse_session_harvest_scrape.resolve_harvest_body",
                resolve_mock,
            ),
            patch(
                "cdp_ask.cse_session_harvest_scrape.asyncio.sleep",
                AsyncMock(),
            ),
        ):
            resp = await harvest_with_loading_wait(
                page,
                req,
                None,
                limit=1,
                chat_url=_CHAT_URL,
            )
        assert resp.outcome == "harvested", f"run={run}"
        assert resp.waited_ms is not None and resp.waited_ms > 0, f"run={run}"


@pytest.mark.asyncio
async def test_output_file_loading_wait_misses_at_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cdp_ask.cse_session_harvest_scrape.OPEN_ON_DEMAND_INCOMPLETE_WAIT_S",
        0.0,
    )
    monkeypatch.setattr(
        "cdp_ask.cse_session_harvest_scrape.OPEN_ON_DEMAND_INCOMPLETE_POLL_S",
        0.0,
    )
    page = MagicMock()
    req = HarvestRequest(source="output-file", chat_url=_CHAT_URL)
    with (
        patch(
            "cdp_ask.cse_session_harvest_scrape.harvest_turns",
            AsyncMock(return_value=_settled_turn_dom("x" * 200)),
        ),
        patch(
            "cdp_ask.cse_session_harvest_scrape.resolve_harvest_body",
            AsyncMock(side_effect=OutputDownloadError("never ready")),
        ),
        patch(
            "cdp_ask.cse_session_harvest_scrape.asyncio.sleep",
            AsyncMock(),
        ),
    ):
        resp = await harvest_with_loading_wait(
            page,
            req,
            None,
            limit=1,
            chat_url=_CHAT_URL,
        )
    assert resp.outcome == "unreachable"
    assert resp.reason == "output_file_miss"
