"""Request-path episode reads parse only the keyed rows, never the whole history (a:36941)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from claude_bundles import cdp_registry_events, cse_provenance
from claude_bundles import cdp_registry_store as store
from claude_bundles import cse_provenance_index as index

pytestmark = pytest.mark.offline

_URLS = 40
_PER_URL = 25
_NOISE = 500


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "cdp-registry"
    (root / "registrations").mkdir(parents=True)
    monkeypatch.setattr(store, "REGISTRY_DIR", root)
    monkeypatch.setattr(store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(store, "REGISTRATIONS_DIR", root / "registrations")
    monkeypatch.setattr(
        cdp_registry_events, "emit", lambda event: None
    )  # keep the event socket out of the test
    return root


def _url(i: int) -> str:
    return f"https://claude.ai/cowork/cse_idx{i:04d}"


def _episode_line(url_i: int, k: int) -> str:
    return json.dumps(
        {
            "event": index.EPISODE_EVENT,
            "ts": time.time(),
            "episode_id": f"ep-{url_i}-{k}",
            "chat_url": _url(url_i),
            "registration_id": f"reg-{url_i}",
            "cdp_url": "http://127.0.0.1:9223",
            "lane_thread": None,
            "parent_thread": None,
            "lane_role": None,
            "state": "bound",
            "evidence_class": "observed",
            "attribution_source": "test",
            "correlation_id": f"exec-{url_i}-{k}",
            "observed_at": float(k),
            "supersedes": f"ep-{url_i}-{k - 1}" if k else None,
            "reason": None,
            "lineage_state": "unresolved",
            "association_id": None,
            "lineage_observed_at": None,
        },
        sort_keys=True,
    )


def _bulk_fill(log: Path) -> int:
    """Interleave episode rows for many URLs with non-episode journal noise."""
    rows = 0
    with log.open("a", encoding="utf-8") as fh:
        for k in range(_PER_URL):
            for url_i in range(_URLS):
                fh.write(_episode_line(url_i, k) + "\n")
                rows += 1
            for n in range(_NOISE // _PER_URL):
                fh.write(json.dumps({"event": "hygiene_reclaim", "n": n}) + "\n")
                rows += 1
    return rows


def _count_parses(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    counter = [0]
    real_loads = index.json.loads

    def _counting(raw, *args, **kwargs):
        counter[0] += 1
        return real_loads(raw, *args, **kwargs)

    monkeypatch.setattr(index.json, "loads", _counting)
    return counter


def test_keyed_lookup_parses_only_matching_rows(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the one-time build, a resolve-shaped read parses |rows for that key|, not the history."""
    total_rows = _bulk_fill(store.REGISTRY_LOG)
    first = index.refresh()
    assert first.rebuilt is True
    assert first.rows_consumed == total_rows
    assert first.rows_indexed == _URLS * _PER_URL

    parses = _count_parses(monkeypatch)
    episodes = cse_provenance.episodes_for_chat_url(_url(7))
    assert [e.episode_id for e in episodes] == [f"ep-7-{k}" for k in range(_PER_URL)]
    assert parses[0] == _PER_URL, "chat_url lookup parsed more rows than it returned"
    assert index.refresh().bytes_consumed == 0

    parses[0] = 0
    assert len(cse_provenance.episodes_for_registration("reg-3")) == _PER_URL
    assert parses[0] == _PER_URL

    parses[0] = 0
    latest = cse_provenance.latest_episode(correlation_id="exec-5-9")
    assert latest is not None and latest.episode_id == "ep-5-9"
    assert parses[0] == 1, "correlation lookup parsed more than one row"

    parses[0] = 0
    assert cse_provenance.latest_episode(chat_url=_url(0) + "/").episode_id == (
        f"ep-0-{_PER_URL - 1}"
    )
    assert parses[0] == 1

    parses[0] = 0
    assert cse_provenance.latest_episode(chat_url="not-a-cse") is None
    assert cse_provenance.latest_episode() is None
    assert parses[0] == 0


def test_keyed_reads_match_full_history_reader(isolated_registry: Path) -> None:
    """Keyed results equal the old whole-journal filter for every lookup key."""
    _bulk_fill(store.REGISTRY_LOG)
    reference = []
    for raw in store.REGISTRY_LOG.read_text(encoding="utf-8").splitlines():
        record = json.loads(raw)
        if record.get("event") == index.EPISODE_EVENT:
            reference.append(record["episode_id"])

    assert [e.episode_id for e in cse_provenance.read_episodes()] == reference
    for url_i in (0, 19, _URLS - 1):
        by_url = [
            e.episode_id for e in cse_provenance.episodes_for_chat_url(_url(url_i))
        ]
        assert by_url == [r for r in reference if r.startswith(f"ep-{url_i}-")]
        by_reg = [
            e.episode_id
            for e in cse_provenance.episodes_for_registration(f"reg-{url_i}")
        ]
        assert by_reg == by_url


def test_rebind_keeps_earlier_episodes_and_resolves_latest(
    isolated_registry: Path,
) -> None:
    """A URL rebound to a new host keeps the first host's episode readable by URL."""
    first = cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_rebind/",
        registration_id="reg-a",
        cdp_url="http://127.0.0.1:9223",
        lane_thread="thread-a",
        correlation_id="exec-a",
    )
    second = cse_provenance.append_episode(
        chat_url="https://claude.ai/cowork/cse_rebind",
        registration_id="reg-b",
        cdp_url="http://127.0.0.1:9224",
        lane_thread="thread-b",
        correlation_id="exec-b",
    )
    assert second.supersedes == first.episode_id
    by_url = cse_provenance.episodes_for_chat_url("https://claude.ai/cowork/cse_rebind")
    assert [e.episode_id for e in by_url] == [first.episode_id, second.episode_id]
    assert cse_provenance.episodes_for_registration("reg-a") == [first]
    assert cse_provenance.latest_episode(correlation_id="exec-a") == first
    resolved = cse_provenance.resolve(chat_url="https://claude.ai/cowork/cse_rebind")
    assert resolved["registration_id"] == "reg-b"
    assert resolved["episode_id"] == second.episode_id


def test_tail_fold_and_truncation_rebuild(isolated_registry: Path) -> None:
    """New journal lines fold in incrementally; a shrunken journal rebuilds the index."""
    log = store.REGISTRY_LOG
    with log.open("a", encoding="utf-8") as fh:
        fh.write(_episode_line(1, 0) + "\n")
    assert index.refresh().rows_indexed == 1
    with log.open("a", encoding="utf-8") as fh:
        fh.write(_episode_line(1, 1) + "\n")
        fh.write('{"event": "cse.provenance.episode", "chat_url": "partial"')
    tail = index.refresh()
    assert tail.rebuilt is False
    assert tail.rows_indexed == 1
    assert [e.episode_id for e in cse_provenance.episodes_for_chat_url(_url(1))] == [
        "ep-1-0",
        "ep-1-1",
    ]
    assert index.refresh().bytes_consumed == 0

    log.write_text(_episode_line(2, 0) + "\n", encoding="utf-8")
    rebuilt = index.refresh()
    assert rebuilt.rebuilt is True
    assert cse_provenance.episodes_for_chat_url(_url(1)) == []
    assert [e.episode_id for e in cse_provenance.episodes_for_chat_url(_url(2))] == [
        "ep-2-0"
    ]


def test_corrupt_index_file_is_rebuilt(isolated_registry: Path) -> None:
    """A non-database file at the index path is replaced rather than raised."""
    with store.REGISTRY_LOG.open("a", encoding="utf-8") as fh:
        fh.write(_episode_line(4, 0) + "\n")
    index.index_path().write_text("not a database", encoding="utf-8")
    assert [e.episode_id for e in cse_provenance.episodes_for_chat_url(_url(4))] == [
        "ep-4-0"
    ]
