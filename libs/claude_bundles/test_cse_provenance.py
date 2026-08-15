"""Offline tests for immutable CSE provenance episodes and session projection."""

from __future__ import annotations

import json
from pathlib import Path

from claude_bundles import cdp_registry_events, cse_provenance


def _capture_signals(monkeypatch) -> list[tuple[str, dict]]:
    """Collect emitted signal/payload pairs instead of reaching the event socket."""
    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        cdp_registry_events,
        "emit",
        lambda event: captured.append((event.signal, event.payload)),
    )
    return captured


def test_rebinding_appends_without_erasing_history(
    monkeypatch, tmp_path: Path
) -> None:
    """Each bind remains readable after a later host becomes current."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)

    first = cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_one/",
        registration_id="reg-a",
        cdp_url="http://127.0.0.1:9223",
        lane_thread="thread-a",
        correlation_id="exec-a",
    )
    second = cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_one",
        registration_id="reg-b",
        cdp_url="http://127.0.0.1:9224",
        lane_thread="thread-b",
        correlation_id="exec-b",
    )

    episodes = cse_provenance.read_episodes()
    assert [episode.episode_id for episode in episodes] == [
        first.episode_id,
        second.episode_id,
    ]
    resolved = cse_provenance.resolve(chat_url="https://claude.ai/cowork/cse_one")
    assert resolved["registration_id"] == "reg-b"


def test_missing_lineage_is_typed_unresolved(monkeypatch, tmp_path: Path) -> None:
    """Missing bus lineage keeps host current and lineage unresolved."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_two",
        registration_id="reg-c",
        cdp_url="http://127.0.0.1:9225",
        lane_thread="thread-c",
    )

    resolved = cse_provenance.resolve(
        chat_url="https://claude.ai/cowork/cse_two",
        lineage_reader=lambda _thread: None,
    )
    assert resolved["state"] == "current"
    assert resolved["lineage_state"] == "unresolved"
    assert resolved["reason"] == "lane_lineage_missing"
    assert "parent_thread_proven" not in resolved
    assert resolved["lane_thread_claim"] == "thread-c"
    assert "lineage_observed_at" in resolved
    assert resolved["lineage_observed_at"] is None


def test_lane_less_bind_records_claim_only(monkeypatch, tmp_path: Path) -> None:
    """Registry host receipts without lane_thread stay unresolved."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    signals = _capture_signals(monkeypatch)

    episode = cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_laneless",
        registration_id="reg-l",
        cdp_url="http://127.0.0.1:9228",
        reason="lane_less_bind",
    )

    assert episode.lane_thread is None
    assert episode.parent_thread is None
    assert episode.lane_role is None
    assert episode.lineage_state == "unresolved"
    assert signals[-2][0] == "cdp.provenance.bound"
    assert signals[-1][0] == "cdp.provenance.unresolved"
    assert signals[-1][1]["reason"] == "lane_less_bind"


def test_unregistered_shadow_url_is_silent(monkeypatch, tmp_path: Path) -> None:
    """Shadow URLs without episodes stay unregistered and emit no unresolved signal."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    signals = _capture_signals(monkeypatch)

    resolved = cse_provenance.resolve(
        chat_url="https://claude.ai/cowork/cse_shadow_only",
    )

    assert resolved["state"] == "unregistered_cse"
    assert resolved["reason"] == "no_episode"
    assert "parent_thread_claim" not in resolved
    assert signals == []


def test_proven_enrichment_projection(monkeypatch, tmp_path: Path) -> None:
    """Proven episodes expose association_id and proven fields, not guessed claims."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_proven",
        registration_id="reg-p",
        cdp_url="http://127.0.0.1:9229",
        lane_thread="thread-p",
    )
    cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_proven",
        registration_id="reg-p",
        cdp_url="http://127.0.0.1:9229",
        lane_thread="thread-p",
        lineage={"parent_thread": "bus-parent", "lane_role": "side"},
        association_id=42,
        lineage_state="proven",
        lineage_observed_at=100.0,
        state="current",
        reason="bus_lane_current",
    )

    resolved = cse_provenance.resolve(chat_url="https://claude.ai/cowork/cse_proven")

    assert resolved["state"] == "current"
    assert resolved["lineage_state"] == "proven"
    assert resolved["association_id"] == 42
    assert resolved["parent_thread_proven"] == "bus-parent"
    assert resolved["lane_thread_claim"] == "thread-p"
    assert resolved["lineage_observed_at"] == 100.0
    assert resolved["source"] == "registry-journal"


def test_rebinding_links_prior_episode_and_reports_it_historical(
    monkeypatch, tmp_path: Path
) -> None:
    """A host change links the prior episode and reports it as no longer current."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    signals = _capture_signals(monkeypatch)

    first = cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_four",
        registration_id="reg-a",
        cdp_url="http://127.0.0.1:9223",
        lane_thread="thread-a",
    )
    second = cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_four",
        registration_id="reg-b",
        cdp_url="http://127.0.0.1:9224",
        lane_thread="thread-b",
    )

    assert first.supersedes is None
    assert second.supersedes == first.episode_id
    historical = [s for s in signals if s[0] == "cdp.provenance.historical"]
    assert [s[1]["episode_id"] for s in historical] == [first.episode_id]


def test_idempotent_rebind_does_not_report_historical(
    monkeypatch, tmp_path: Path
) -> None:
    """Re-binding the same host is not a host change and must stay quiet."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    signals = _capture_signals(monkeypatch)

    for _ in range(2):
        cse_provenance.append_episode(
            chat_url="https://claude.ai/cowork/cse_five",
            registration_id="reg-same",
            cdp_url="http://127.0.0.1:9223",
            lane_thread="thread-a",
        )

    assert not [s for s in signals if s[0] == "cdp.provenance.historical"]


def test_stale_host_claiming_url_resolves_conflict(monkeypatch, tmp_path: Path) -> None:
    """A host that no longer holds the URL must not receive the current evidence."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_six",
        registration_id="reg-old",
        cdp_url="http://127.0.0.1:9223",
        lane_thread="thread-a",
    )
    cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_six",
        registration_id="reg-new",
        cdp_url="http://127.0.0.1:9224",
        lane_thread="thread-b",
    )
    signals = _capture_signals(monkeypatch)

    resolved = cse_provenance.resolve(
        chat_url="https://claude.ai/cowork/cse_six",
        registration_id="reg-old",
    )

    assert resolved["state"] == "conflict"
    assert resolved["current_registration_id"] == "reg-new"
    assert resolved["candidate_count"] == 2
    assert len(resolved["candidates"]) == 2
    assert all("episode_id" in candidate for candidate in resolved["candidates"])
    assert all("host_state" in candidate for candidate in resolved["candidates"])
    assert [s[0] for s in signals] == ["cdp.provenance.conflict"]


def test_missing_lineage_reports_unresolved_signal(monkeypatch, tmp_path: Path) -> None:
    """Evidence that cannot complete a lineage join is reported, not silent."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_seven",
        registration_id="reg-e",
        cdp_url="http://127.0.0.1:9227",
        lane_thread="thread-e",
    )
    signals = _capture_signals(monkeypatch)

    cse_provenance.resolve(
        chat_url="https://claude.ai/cowork/cse_seven",
        lineage_reader=lambda _thread: None,
    )

    assert [s[0] for s in signals] == ["cdp.provenance.unresolved"]
    assert signals[0][1]["reason"] == "lane_lineage_missing"


def test_unbound_lane_stays_silent(monkeypatch, tmp_path: Path) -> None:
    """A lane with no episode yet is ordinary and must not emit per lookup."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    signals = _capture_signals(monkeypatch)

    resolved = cse_provenance.resolve(registration_id="reg-never-bound")

    assert resolved["state"] == "unresolved"
    assert resolved["reason"] == "no_episode"
    assert signals == []


def test_historical_registration_hides_proven_fields(monkeypatch, tmp_path: Path) -> None:
    """Released hosts resolve historical without current or proven lane keys."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(
        "claude_bundles.cse_provenance_resolve.is_host_listable",
        lambda _rid: False,
    )
    cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_hist",
        registration_id="reg-h",
        cdp_url="http://127.0.0.1:9230",
        lane_thread="thread-h",
        lineage={"parent_thread": "bus-h", "lane_role": "side"},
        association_id=7,
        lineage_state="proven",
    )

    resolved = cse_provenance.resolve(
        registration_id="reg-h",
        host_listable=lambda _rid: False,
    )

    assert resolved["state"] == "historical"
    assert "parent_thread_proven" not in resolved
    assert "association_id" not in resolved
    assert "lane_thread_claim" not in resolved


def test_episode_is_durable_json_record(monkeypatch, tmp_path: Path) -> None:
    """The append leaves a readable registry journal record, not only memory."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    episode = cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_three",
        registration_id="reg-d",
        cdp_url="http://127.0.0.1:9226",
        lane_thread=None,
    )
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert records[0]["event"] == "cse.provenance.episode"
    assert records[0]["episode_id"] == episode.episode_id


def test_legacy_row_infers_claimed_not_proven(monkeypatch, tmp_path: Path) -> None:
    """Legacy journal rows with lane claims infer claimed and never become proven."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    legacy = {
        "event": "cse.provenance.episode",
        "episode_id": "legacy-1",
        "chat_url": "https://claude.ai/cowork/cse_legacy",
        "registration_id": "reg-legacy",
        "cdp_url": "http://127.0.0.1:9223",
        "lane_thread": "thread-legacy",
        "parent_thread": "parent-legacy",
        "lineage_state": "proven",
        "state": "bound",
        "evidence_class": "observed",
        "attribution_source": "cdp-registry",
        "correlation_id": None,
        "observed_at": 1.0,
        "supersedes": None,
    }
    log.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    episode = cse_provenance.read_episodes()[0]
    assert episode.lineage_state == "claimed"
    assert episode.association_id is None


def test_reader_overlay_requires_association_id(monkeypatch, tmp_path: Path) -> None:
    """Associated lineage without association_id stays unresolved."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_assoc",
        registration_id="reg-a",
        cdp_url="http://127.0.0.1:9223",
        lane_thread="thread-a",
    )

    resolved = cse_provenance.resolve(
        chat_url="https://claude.ai/cowork/cse_assoc",
        lineage_reader=lambda _thread: {
            "state": "associated",
            "parent_thread": "parent",
            "lane_role": "side",
        },
    )

    assert resolved["state"] == "current"
    assert resolved["lineage_state"] == "unresolved"
    assert "parent_thread_proven" not in resolved


def test_proven_append_rejects_missing_association_id(monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)

    try:
        cse_provenance.append_episode(
            chat_url="https://claude.ai/cowork/cse_bad",
            registration_id="reg-bad",
            cdp_url="http://127.0.0.1:9223",
            lane_thread="thread-b",
            lineage_state="proven",
        )
    except ValueError as exc:
        assert "association_id" in str(exc)
    else:
        raise AssertionError("expected ValueError for proven without association_id")


def test_bind_registry_lane_role_never_becomes_proof(
    monkeypatch, tmp_path: Path
) -> None:
    """Registry lane_role at bind must not populate episode parent_thread or lane_role."""
    from claude_bundles.cdp_registry import session_address as reg

    root = tmp_path / "cdp-registry"
    root.mkdir()
    log = root / "registry.jsonl"
    active_json = root / "active.json"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg._store, "REGISTRY_LOG", log)
    monkeypatch.setattr(reg._store, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg._store, "ACTIVE_JSON", active_json)
    monkeypatch.setattr(reg._store, "PORTS_LOCK", root / "ports.lock")
    reg._store.write_active(
        {
            "reg-bind": {
                "registration_id": "reg-bind",
                "port": 9223,
                "status": "active",
                "parent_thread": "thread-claim",
                "lane_role": "side",
            }
        }
    )

    assert reg.bind_session_address(
        "reg-bind",
        chat_url="https://claude.ai/cowork/cse_bind_role",
    )

    episode = cse_provenance.read_episodes()[-1]
    assert episode.lane_thread == "thread-claim"
    assert episode.lane_role is None
    assert episode.parent_thread is None
    assert episode.lineage_state != "proven"


def test_resolver_emits_unresolved_when_lane_thread_missing(
    monkeypatch, tmp_path: Path
) -> None:
    """A supplied reader on a lane-less episode emits unresolved before missing reason."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    signals = _capture_signals(monkeypatch)
    cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_no_lane",
        registration_id="reg-nl",
        cdp_url="http://127.0.0.1:9223",
    )

    resolved = cse_provenance.resolve(
        chat_url="https://claude.ai/cowork/cse_no_lane",
        lineage_reader=lambda _thread: {"state": "associated", "association_id": 1},
    )

    assert resolved["lineage_state"] == "unresolved"
    assert resolved["reason"] == "lane_lineage_missing"
    unresolved = [s for s in signals if s[0] == "cdp.provenance.unresolved"]
    assert len(unresolved) == 2
    assert unresolved[-1][1]["reason"] == "lane_lineage_missing"
