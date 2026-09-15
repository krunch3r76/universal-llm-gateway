"""Hermetic tests for harvest identity → Cowork URL resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cdp_ask.cse_session_harvest_identity import (
    chat_url_from_archives,
    chat_url_from_provenance,
    cse_url_from_token,
    resolve_execution_provenance,
    resolve_harvest_chat_url,
    satellite_id_from_inflight,
)
from cdp_ask.followup_envelope import FollowupCandidate
from cdp_ask.cse_session_models import CseSessionTurn, HarvestRequest, HarvestResponse
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


def test_archive_header_lookup_by_stargate_id(tmp_path: Path) -> None:
    sat = "2ba8da0ac9254bc08ffcefdbeb11db84"
    stargate = "eddc877e-3f63-439a-a115-994b2856200f"
    (tmp_path / f"cdp-ask-archive-sonnet-5-{sat}.md").write_text(
        "# CDP ask harvest\n\n"
        f"- execution_id: `{sat}`\n"
        f"- stargate_execution_id: `{stargate}`\n"
        "- url: `https://claude.ai/cowork/cse_015Wj9BxzFrBhp6D5jPoQW7D`\n",
        encoding="utf-8",
    )
    assert chat_url_from_archives(stargate, archive_dir=tmp_path) == (
        "https://claude.ai/cowork/cse_015Wj9BxzFrBhp6D5jPoQW7D"
    )


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
async def test_harvest_refuses_unresolved_lineage_before_foreign_scrape() -> None:
    """a:34029 — unresolved lineage must refuse before any CSE scrape."""
    from cdp_ask.cse_session_harvest import execute_harvest

    execution_id = "4a1070a2-94b9-4f7e-9fd4-1b6efad5b7cf"
    foreign_cse = "https://claude.ai/cowork/cse_01LE8ddPWjPg3Z2KP6jnvguS"
    unresolved_prov = {
        "state": "current",
        "lineage_state": "unresolved",
        "reason": "hygiene_drain",
        "chat_url": "https://claude.ai/cowork/cse_requestedMission",
        "registration_id": "reg-unresolved",
        "execution_id": execution_id,
        "correlation_id": execution_id,
    }
    store = ExecutionStore()
    with (
        patch(
            "cdp_ask.cse_session_harvest.resolve_execution_provenance",
            AsyncMock(return_value=unresolved_prov),
        ),
        patch(
            "cdp_ask.cse_session_harvest.discover_candidates",
            AsyncMock(
                return_value=(
                    [
                        FollowupCandidate(
                            registration_id="reg-live",
                            chat_url=foreign_cse,
                            holder="h",
                            purpose="ask",
                            cdp_url="http://127.0.0.1:9222",
                        )
                    ],
                    "execution_id",
                    execution_id,
                )
            ),
        ),
        patch(
            "cdp_ask.cse_session_harvest.connect_cdp",
            AsyncMock(),
        ) as connect,
        patch(
            "cdp_ask.cse_session_harvest.harvest_by_opening_url",
            AsyncMock(),
        ) as opener,
        patch(
            "cdp_ask.cse_session_harvest.harvest_page",
            AsyncMock(),
        ) as harvest_page,
        patch("cdp_ask.cse_session_harvest.emit", lambda _event: None),
    ):
        result = await execute_harvest(
            HarvestRequest(execution_id=execution_id),
            store,
        )
    connect.assert_not_called()
    opener.assert_not_awaited()
    harvest_page.assert_not_awaited()
    assert result.outcome == "refused"
    assert result.reason == "lineage_unresolved"
    assert result.provenance is not None
    assert result.provenance["execution_id"] == execution_id
    assert result.provenance["registration_id"] == "reg-unresolved"
    assert result.provenance["lineage_state"] == "unresolved"
    assert result.turns == []


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
    assert result.chat_url == "https://claude.ai/cowork/cse_x"


@pytest.mark.asyncio
async def test_store_get_aliases_stargate_execution_id() -> None:
    store = ExecutionStore()
    rec = await store.create(
        holder="cursor",
        purpose="ask",
        stargate_execution_id="sg-exec-alias",
    )
    aliased = await store.get("sg-exec-alias")
    assert aliased is not None
    assert aliased.execution_id == rec.execution_id
    assert (await store.get(rec.execution_id)).execution_id == rec.execution_id


def test_chat_url_from_provenance_matches_correlation() -> None:
    episode = type("E", (), {})()
    episode.correlation_id = "sat-abc"
    episode.chat_url = "https://claude.ai/cowork/cse_provHarvest1"
    with patch(
        "claude_bundles.cse_provenance.read_episodes",
        return_value=[episode],
    ):
        assert chat_url_from_provenance("sat-abc") == (
            "https://claude.ai/cowork/cse_provHarvest1"
        )
        assert chat_url_from_provenance("other") is None


def test_satellite_id_from_inflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db = tmp_path / "stargate-cdp-generate-inflight.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE cdp_inflight_leg ("
        "execution_id TEXT PRIMARY KEY, satellite_execution_id TEXT)"
    )
    conn.execute(
        "INSERT INTO cdp_inflight_leg VALUES (?, ?)",
        ("sg-stargate-1", "sat-from-inflight"),
    )
    conn.commit()
    conn.close()
    assert satellite_id_from_inflight("sg-stargate-1") == "sat-from-inflight"
    assert satellite_id_from_inflight("missing") is None


@pytest.mark.asyncio
async def test_stargate_id_opens_via_inflight_then_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cdp_ask.cse_session_harvest_identity.satellite_id_from_inflight",
        lambda token: "sat-from-inflight" if token == "sg-stargate-1" else None,
    )
    monkeypatch.setattr(
        "cdp_ask.cse_session_harvest_identity.chat_url_from_archives",
        lambda token: None,
    )
    monkeypatch.setattr(
        "cdp_ask.cse_session_harvest_identity.chat_url_from_provenance",
        lambda token: (
            "https://claude.ai/cowork/cse_fromProv"
            if token == "sat-from-inflight"
            else None
        ),
    )
    url = await resolve_harvest_chat_url(
        HarvestRequest(execution_id="sg-stargate-1"),
        ExecutionStore(),
    )
    assert url == "https://claude.ai/cowork/cse_fromProv"


@pytest.mark.asyncio
async def test_metadata_only_harvest_identity_unverified() -> None:
    from cdp_ask.cse_session_harvest import harvest_page

    page = MagicMock()
    page.url = "https://claude.ai/cowork/cse_meta"
    result = await harvest_page(
        page,
        HarvestRequest(registration_id="req-reg", metadata_only=True),
        provenance={"registration_id": "minted-reg"},
        requested_registration_id="req-reg",
        requested_chat_url="https://claude.ai/cowork/cse_meta",
    )
    assert result.identity is not None
    assert result.identity.identity_check == "unverified"


@pytest.mark.asyncio
async def test_harvest_foreign_transcript_conflict() -> None:
    from cdp_ask.cse_session_harvest import harvest_page

    page = MagicMock()
    page.url = "https://claude.ai/cowork/cse_observed"
    with patch(
        "cdp_ask.cse_session_harvest.harvest_with_loading_wait",
        AsyncMock(
            return_value=HarvestResponse(
                outcome="harvested",
                turns=[
                    CseSessionTurn(
                        author="assistant",
                        text="wrong seat content long enough.",
                        source="cse-dom",
                    )
                ],
                provenance={"registration_id": "minted-reg"},
            )
        ),
    ):
        result = await harvest_page(
            page,
            HarvestRequest(registration_id="req-reg"),
            provenance={"registration_id": "minted-reg"},
            requested_registration_id="req-reg",
            requested_chat_url="https://claude.ai/cowork/cse_requested",
        )
    assert result.outcome == "conflict"
    assert result.reason == "foreign_transcript"
    assert result.turns == []
    assert result.identity is not None
    assert result.identity.identity_check == "foreign_transcript"


@pytest.mark.asyncio
async def test_harvest_registration_drift_after_reattach_mint() -> None:
    from cdp_ask.cse_session_harvest import harvest_page

    cse = "https://claude.ai/cowork/cse_same"
    page = MagicMock()
    page.url = cse
    with patch(
        "cdp_ask.cse_session_harvest.harvest_with_loading_wait",
        AsyncMock(
            return_value=HarvestResponse(
                outcome="harvested",
                turns=[
                    CseSessionTurn(
                        author="assistant",
                        text="harvested body long enough.",
                        source="cse-dom",
                    )
                ],
                provenance={"registration_id": "minted-reg", "opened_on_demand": True},
            )
        ),
    ):
        result = await harvest_page(
            page,
            HarvestRequest(registration_id="req-reg"),
            provenance={"registration_id": "minted-reg", "opened_on_demand": True},
            requested_registration_id="req-reg",
            requested_chat_url=cse,
        )
    assert result.outcome == "harvested"
    assert len(result.turns) == 1
    assert result.identity is not None
    assert result.identity.identity_check == "registration_drift"
    assert result.identity.requested_registration_id == "req-reg"
    assert result.identity.current_registration_id == "minted-reg"
    assert result.identity.requested_registration_id != result.identity.current_registration_id
