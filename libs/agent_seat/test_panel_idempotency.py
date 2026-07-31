"""Unit tests for panel_dispatch R8 idempotency store."""

from __future__ import annotations

from typing import Any

import pytest

from agent_seat import panel_idempotency as pidem


def _base_fp_kwargs() -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": "review"}],
        "dispatch_thread_id": "thread-1",
        "disposition": "panel",
        "include_synthesizer": False,
        "system": "",
        "source_ref": None,
        "reasoning_effort": None,
        "generation_options": None,
        "max_tool_turns": None,
        "timeout_seconds": None,
    }


def test_fingerprint_stable_across_noncost_fields() -> None:
    base = _base_fp_kwargs()
    fp_a = pidem.build_panel_request_fingerprint(**base)
    fp_b = pidem.build_panel_request_fingerprint(**base)
    assert fp_a == fp_b


def test_fingerprint_changes_on_messages() -> None:
    base = _base_fp_kwargs()
    fp_a = pidem.build_panel_request_fingerprint(**base)
    alt = {**base, "messages": [{"role": "user", "content": "different"}]}
    fp_b = pidem.build_panel_request_fingerprint(**alt)
    assert fp_a != fp_b


def test_fingerprint_changes_on_include_synthesizer() -> None:
    base = _base_fp_kwargs()
    fp_a = pidem.build_panel_request_fingerprint(**base)
    alt = {**base, "include_synthesizer": True}
    fp_b = pidem.build_panel_request_fingerprint(**alt)
    assert fp_a != fp_b


def test_fingerprint_changes_on_source_ref() -> None:
    base = _base_fp_kwargs()
    fp_a = pidem.build_panel_request_fingerprint(**base)
    alt = {**base, "source_ref": "tmp/prompts/foo.md"}
    fp_b = pidem.build_panel_request_fingerprint(**alt)
    assert fp_a != fp_b


@pytest.fixture(autouse=True)
def _clear_store() -> None:
    pidem._store.clear()
    yield
    pidem._store.clear()


def test_check_or_reserve_miss_returns_reserved() -> None:
    fp = pidem.build_panel_request_fingerprint(**_base_fp_kwargs())
    result = pidem.check_or_reserve("req-1", fp)
    assert result.kind == "reserved"
    assert "req-1" in pidem._store


def test_hit_after_commit_same_fingerprint() -> None:
    fp = pidem.build_panel_request_fingerprint(**_base_fp_kwargs())
    env = {"panel_executions": {"skeptic": "exec-1"}}
    pidem.check_or_reserve("req-2", fp)
    pidem.commit("req-2", env)
    result = pidem.check_or_reserve("req-2", fp)
    assert result.kind == "hit"
    assert result.envelope == env
    assert result.age_s >= 0


def test_in_flight_on_pending_same_fingerprint() -> None:
    fp = pidem.build_panel_request_fingerprint(**_base_fp_kwargs())
    pidem.check_or_reserve("req-3", fp)
    result = pidem.check_or_reserve("req-3", fp)
    assert result.kind == "in_flight"


def test_conflict_on_fingerprint_mismatch() -> None:
    fp_a = pidem.build_panel_request_fingerprint(**_base_fp_kwargs())
    alt = {**_base_fp_kwargs(), "messages": [{"role": "user", "content": "other"}]}
    fp_b = pidem.build_panel_request_fingerprint(**alt)
    pidem.check_or_reserve("req-4", fp_a)
    pidem.commit("req-4", {"panel_executions": {"skeptic": "exec-1"}})
    result = pidem.check_or_reserve("req-4", fp_b)
    assert result.kind == "conflict"


def test_release_allows_fresh_reserve() -> None:
    fp = pidem.build_panel_request_fingerprint(**_base_fp_kwargs())
    pidem.check_or_reserve("req-5", fp)
    pidem.release("req-5")
    result = pidem.check_or_reserve("req-5", fp)
    assert result.kind == "reserved"


def test_prune_expired_drops_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    fp = pidem.build_panel_request_fingerprint(**_base_fp_kwargs())
    past = 1000.0
    monkeypatch.setattr(pidem, "_TTL_SECONDS", 600.0)
    pidem.check_or_reserve("req-6", fp, now=past)
    pidem.commit("req-6", {"panel_executions": {"skeptic": "exec-1"}}, now=past)
    result = pidem.check_or_reserve("req-6", fp, now=past + 601.0)
    assert result.kind == "reserved"


def test_capacity_evicts_oldest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pidem, "_CAPACITY", 2)
    fp = pidem.build_panel_request_fingerprint(**_base_fp_kwargs())
    pidem.check_or_reserve("old-id", fp, now=100.0)
    pidem.commit("old-id", {"panel_executions": {"skeptic": "e1"}}, now=100.0)
    pidem.check_or_reserve("mid-id", fp, now=200.0)
    pidem.commit("mid-id", {"panel_executions": {"skeptic": "e2"}}, now=200.0)
    pidem.check_or_reserve("new-id", fp, now=300.0)
    pidem.commit("new-id", {"panel_executions": {"skeptic": "e3"}}, now=300.0)
    result = pidem.check_or_reserve("old-id", fp, now=400.0)
    assert result.kind == "reserved"


def test_disabled_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pidem, "_DISABLED", True)
    assert pidem.disabled() is True
