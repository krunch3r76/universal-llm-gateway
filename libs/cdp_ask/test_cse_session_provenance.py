"""Hermetic tests for public cse_session provenance projection."""

from __future__ import annotations

from pathlib import Path

import pytest
from claude_bundles import cse_provenance

from cdp_ask.cse_session_models import ProvenanceQuery
from cdp_ask.cse_session_provenance import resolve_public_provenance


@pytest.fixture(autouse=True)
def _mute_events(monkeypatch) -> None:
    monkeypatch.setattr("cdp_ask.cse_session_provenance.emit", lambda event: None)


@pytest.mark.asyncio
async def test_claim_proven_separation(monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(
        "cdp_ask.cse_session_provenance.is_host_listable",
        lambda _reg: True,
    )
    cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_claim",
        registration_id="reg-claim",
        cdp_url="http://127.0.0.1:9223",
        lane_thread="thread-claim",
        lineage={"parent_thread": "parent-claim"},
    )
    cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_claim",
        registration_id="reg-claim",
        cdp_url="http://127.0.0.1:9223",
        lane_thread="thread-claim",
        lineage={"parent_thread": "bus-parent", "lane_role": "side"},
        association_id=7,
        lineage_state="proven",
        lineage_observed_at=100.0,
    )
    result = await resolve_public_provenance(
        ProvenanceQuery(chat_url="https://claude.ai/cowork/cse_claim")
    )
    assert result.parent_thread_proven == "bus-parent"
    assert result.lane_thread_claim == "thread-claim"
    dumped = result.model_dump(exclude_none=True)
    assert "execution_id" not in dumped


@pytest.mark.asyncio
async def test_self_supersession_conflict(monkeypatch, tmp_path: Path) -> None:
    result = await resolve_public_provenance(
        ProvenanceQuery(
            predecessor_registration_id="same-reg",
            successor_registration_id="same-reg",
        )
    )
    assert result.state == "conflict"
    assert result.reason == "self_supersession"


@pytest.mark.asyncio
async def test_n_seat_candidates(monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "registry.jsonl"
    active = tmp_path / "active.json"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(cse_provenance.store, "ACTIVE_JSON", active)
    import json

    active.write_text(
        json.dumps(
            {
                "reg-a": {
                    "registration_id": "reg-a",
                    "cdp_url": "http://127.0.0.1:9223",
                    "port": 9223,
                    "holder": "h",
                    "purpose": "ask",
                    "status": "active",
                    "chat_url": "https://claude.ai/cowork/cse_a",
                    "profile_suffix": "a",
                },
                "reg-b": {
                    "registration_id": "reg-b",
                    "cdp_url": "http://127.0.0.1:9224",
                    "port": 9224,
                    "holder": "h",
                    "purpose": "ask",
                    "status": "active",
                    "chat_url": "https://claude.ai/cowork/cse_b",
                    "profile_suffix": "b",
                },
                "reg-c": {
                    "registration_id": "reg-c",
                    "cdp_url": "http://127.0.0.1:9225",
                    "port": 9225,
                    "holder": "h",
                    "purpose": "ask",
                    "status": "active",
                    "chat_url": "https://claude.ai/cowork/cse_c",
                    "profile_suffix": "c",
                },
            }
        ),
        encoding="utf-8",
    )
    shared_lane = {"parent_thread": "lane-7246"}
    for reg, url in (
        ("reg-a", "https://claude.ai/cowork/cse_a"),
        ("reg-b", "https://claude.ai/cowork/cse_b"),
        ("reg-c", "https://claude.ai/cowork/cse_c"),
    ):
        cse_provenance.append_episode(
            chat_url=url,
            registration_id=reg,
            cdp_url="http://127.0.0.1:9223",
            lane_thread=f"thread-{reg}",
            lineage=shared_lane,
        )
    result = await resolve_public_provenance(ProvenanceQuery())
    assert isinstance(result, dict)
    candidates = result["candidates"]
    assert len(candidates) == 3
    names = {row["registration_id"] for row in candidates}
    assert names == {"reg-a", "reg-b", "reg-c"}
    url_by_reg = {row["registration_id"]: row["chat_url"] for row in candidates}
    assert url_by_reg == {
        "reg-a": "https://claude.ai/cowork/cse_a",
        "reg-b": "https://claude.ai/cowork/cse_b",
        "reg-c": "https://claude.ai/cowork/cse_c",
    }
    for row in candidates:
        prov = row.get("provenance") or {}
        assert prov.get("state") != "conflict", row
