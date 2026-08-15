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
    """A non-root episode cannot claim lineage after the reader reports missing."""
    log = tmp_path / "registry.jsonl"
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", log)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", tmp_path)
    cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_two",
        registration_id="reg-c",
        cdp_url="http://127.0.0.1:9225",
        lane_thread="thread-c",
        lineage={"parent_thread": "parent-c"},
    )

    resolved = cse_provenance.resolve(
        chat_url="https://claude.ai/cowork/cse_two",
        lineage_reader=lambda _thread: None,
    )
    assert resolved["state"] == "unresolved"
    assert resolved["reason"] == "lane_lineage_missing"


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
        lineage={"parent_thread": "parent-e"},
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
