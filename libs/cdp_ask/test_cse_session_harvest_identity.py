"""Hermetic tests for harvest identity → Cowork URL resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cdp_ask.cse_session_harvest_identity import (
    chat_url_from_archives,
    cse_url_from_token,
    resolve_harvest_chat_url,
)
from cdp_ask.cse_session_models import HarvestRequest
from cdp_ask.execution_store import ExecutionStore


def test_cse_token_and_url_normalize() -> None:
    assert cse_url_from_token("cse_015Wj9BxzFrBhp6D5jPoQW7D") == (
        "https://claude.ai/cowork/cse_015Wj9BxzFrBhp6D5jPoQW7D"
    )
    assert cse_url_from_token(
        "https://claude.ai/cowork/cse_015Wj9BxzFrBhp6D5jPoQW7D/"
    ) == "https://claude.ai/cowork/cse_015Wj9BxzFrBhp6D5jPoQW7D"


def test_archive_filename_and_header_lookup(tmp_path: Path) -> None:
    exe = "2ba8da0ac9254bc08ffcefdbeb11db84"
    body = (
        "# CDP ask harvest\n\n"
        f"- execution_id: `{exe}`\n"
        "- url: `https://claude.ai/cowork/cse_015Wj9BxzFrBhp6D5jPoQW7D`\n"
    )
    path = tmp_path / f"cdp-ask-archive-cdp-opus-{exe}.md"
    path.write_text(body, encoding="utf-8")
    assert chat_url_from_archives(exe, archive_dir=tmp_path) == (
        "https://claude.ai/cowork/cse_015Wj9BxzFrBhp6D5jPoQW7D"
    )
    assert chat_url_from_archives("missing", archive_dir=tmp_path) is None


@pytest.mark.asyncio
async def test_execution_id_opens_via_archive(tmp_path: Path) -> None:
    exe = "2ba8da0ac9254bc08ffcefdbeb11db84"
    (tmp_path / f"cdp-ask-archive-cdp-opus-{exe}.md").write_text(
        f"- execution_id: `{exe}`\n"
        "- url: `https://claude.ai/cowork/cse_015Wj9BxzFrBhp6D5jPoQW7D`\n",
        encoding="utf-8",
    )
    store = ExecutionStore()
    with patch(
        "cdp_ask.cse_session_harvest_identity.chat_url_from_archives",
        wraps=lambda token: chat_url_from_archives(token, archive_dir=tmp_path),
    ):
        url = await resolve_harvest_chat_url(
            HarvestRequest(execution_id=exe),
            store,
        )
    assert url == "https://claude.ai/cowork/cse_015Wj9BxzFrBhp6D5jPoQW7D"


@pytest.mark.asyncio
async def test_harvest_execution_id_opens_when_store_missed() -> None:
    from cdp_ask.cse_session_harvest import execute_harvest
    from cdp_ask.cse_session_models import HarvestResponse

    opened = HarvestResponse(
        outcome="harvested",
        provenance={"opened_on_demand": True},
    )
    store = ExecutionStore()
    with (
        patch(
            "cdp_ask.cse_session_harvest.discover_candidates",
            AsyncMock(return_value=([], None, None)),
        ),
        patch(
            "cdp_ask.cse_session_harvest.resolve_harvest_chat_url",
            AsyncMock(return_value="https://claude.ai/cowork/cse_x"),
        ),
        patch(
            "cdp_ask.cse_session_harvest.harvest_by_opening_url",
            AsyncMock(return_value=opened),
        ) as opener,
        patch("cdp_ask.cse_session_harvest.emit", lambda _event: None),
    ):
        result = await execute_harvest(
            HarvestRequest(execution_id="2ba8da0ac9254bc08ffcefdbeb11db84"),
            store,
        )
    opener.assert_awaited_once()
    assert result.outcome == "harvested"
