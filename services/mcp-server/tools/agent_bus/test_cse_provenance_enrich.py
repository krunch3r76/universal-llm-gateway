"""Tests for hub-side provenance enrichment."""

from __future__ import annotations

from pathlib import Path

from claude_bundles import cdp_registry_events, cse_provenance

from tools.agent_bus.cse_provenance_enrich import enrich_request_provenance


def _associated_reader(_thread: str) -> dict[str, object]:
    return {
        "parent_thread": "bus-parent",
        "lane_role": "side",
        "association_id": 11,
        "state": "associated",
        "lineage_observed_at": 100.0,
    }


def _capture_signals(monkeypatch) -> list[tuple[str, dict]]:
    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        cdp_registry_events,
        "emit",
        lambda event: captured.append((event.signal, event.payload)),
    )
    return captured


def test_enrichment_appends_proven_supersede(monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)

    first = cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_enrich",
        registration_id="reg-e",
        cdp_url="http://127.0.0.1:9223",
        lane_thread="thread-e",
        reason="lane_less_bind",
    )
    prior_bytes = log.read_bytes()

    result = enrich_request_provenance(
        lane_thread="thread-e",
        chat_url="https://claude.ai/cowork/cse_enrich",
        lineage_reader=_associated_reader,
    )

    assert result["ok"] is True
    assert result["lineage_state"] == "proven"
    episodes = cse_provenance.read_episodes()
    assert len(episodes) == 2
    assert episodes[0].episode_id == first.episode_id
    assert episodes[1].supersedes == first.episode_id
    assert episodes[1].lineage_state == "proven"
    assert episodes[1].association_id == 11
    assert episodes[1].parent_thread == "bus-parent"
    assert episodes[1].reason == "bus_lane_current"
    assert log.read_bytes().startswith(prior_bytes)


def test_enrichment_unreachable_never_copies_claim_to_proven(
    monkeypatch, tmp_path: Path
) -> None:
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    _capture_signals(monkeypatch)

    cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_unreachable",
        registration_id="reg-u",
        cdp_url="http://127.0.0.1:9224",
        lane_thread="thread-u",
        lineage_state="unresolved",
    )

    def _unreachable_reader(_thread: str) -> dict[str, object]:
        from tools.agent_bus.cse_lineage_reader import LaneLineageUnreachable

        raise LaneLineageUnreachable("down")

    result = enrich_request_provenance(
        lane_thread="thread-u",
        chat_url="https://claude.ai/cowork/cse_unreachable",
        lineage_reader=_unreachable_reader,
    )

    assert result["ok"] is False
    assert result["reason"] == "lane_lineage_unreachable"
    latest = cse_provenance.read_episodes()[-1]
    assert latest.lineage_state == "unresolved"
    assert latest.association_id is None
    assert latest.parent_thread is None
    assert latest.lane_thread == "thread-u"


def test_enrichment_missing_chat_url_is_insufficient_identity(
    monkeypatch, tmp_path: Path
) -> None:
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_no_url",
        registration_id="reg-x",
        cdp_url="http://127.0.0.1:9226",
        lane_thread="thread-x",
    )

    result = enrich_request_provenance(
        lane_thread="thread-x",
        registration_id="reg-x",
    )

    assert result == {"ok": False, "reason": "insufficient_identity"}
    assert len(cse_provenance.read_episodes()) == 1


def test_enrichment_none_appends_lane_lineage_none(
    monkeypatch, tmp_path: Path
) -> None:
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)

    cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_none",
        registration_id="reg-n",
        cdp_url="http://127.0.0.1:9225",
        lane_thread="thread-n",
    )

    result = enrich_request_provenance(
        lane_thread="thread-n",
        chat_url="https://claude.ai/cowork/cse_none",
        lineage_reader=lambda _thread: None,
    )

    assert result["ok"] is False
    assert result["reason"] == "lane_lineage_none"
    latest = cse_provenance.read_episodes()[-1]
    assert latest.reason == "lane_lineage_none"
