from __future__ import annotations

import importlib

import pytest

G1_TEST_PORT_RANGE = range(19100, 19110)
EVENT_SINK = "browser_governance.events.emit_browser_event"


@pytest.fixture
def sentinel_missing(tmp_path, monkeypatch):
    p = tmp_path / "sentinel-missing"
    monkeypatch.setenv("ULG_BROWSER_STORAGE_SENTINEL", str(p))
    return p


@pytest.fixture
def emitted(monkeypatch):
    events = []
    monkeypatch.setattr(EVENT_SINK, lambda e, **kw: events.append(e) or True)
    return events


def _storage_callers(events):
    return [e.payload["caller"] for e in events if e.signal == "browser.storage.unavailable"]


def _storage_unavailable_class():
    models = importlib.import_module("claude_bundles.cdp_registry.models")
    return getattr(models, "StorageUnavailable", Exception)


def test_v1_1_hygiene_noop_when_storage_sentinel_absent(
    g1_registry_isolation, sentinel_missing, emitted
):
    from claude_bundles.cdp_registry import hygiene as reg
    from claude_bundles.cdp_registry_store import load_active, write_active

    rid = "v1-retained-1"
    profile = g1_registry_isolation.tmp_path / "profiles" / "p1"
    profile.mkdir(parents=True)
    write_active({rid: {"status": "retained", "port": G1_TEST_PORT_RANGE[0], "profile": str(profile)}})
    profile.rmdir()
    before = load_active()
    result = reg.hygiene_reclaim_extended(is_listening=lambda _p: False, empty_trash=False)
    assert load_active().get(rid, {}).get("status") == "retained"
    assert load_active() == before
    assert (list(result.reclaimed_ports), list(result.removed_profiles)) == ([], [])
    assert _storage_callers(emitted) == ["hygiene"]


def test_v1_2_register_lane_refused_when_storage_sentinel_absent(
    g1_registry_isolation, sentinel_missing, emitted
):
    from claude_bundles.cdp_registry import lifecycle as reg
    from claude_bundles.cdp_registry_store import load_active

    exc = _storage_unavailable_class()
    before = load_active()
    recorded = []
    with pytest.raises(exc) as refused:
        reg.register_lane(
            holder="h1",
            purpose="ask",
            launch_chrome=lambda *_: recorded.append(1),
            is_listening=lambda _p: False,
        )
    assert not recorded
    assert load_active() == before
    assert refused.value.retryable is True
    assert refused.value.code == "storage.unavailable"
    assert _storage_callers(emitted) == ["register_lane"]


def test_v1_3_reserve_allocating_row_refused_when_storage_sentinel_absent(
    g1_registry_isolation, sentinel_missing, emitted
):
    from claude_bundles.cdp_registry import lifecycle
    from claude_bundles.cdp_registry_store import load_active

    exc = _storage_unavailable_class()
    before = load_active()
    with pytest.raises(exc):
        lifecycle.reserve_allocating_row(
            holder="h2",
            purpose="ask",
            mission_kind="root",
            parent_thread=None,
            listen=lambda _p: False,
            launch=False,
        )
    assert load_active() == before
    assert _storage_callers(emitted) == ["reserve_allocating_row"]


def test_v1_3b_relaunch_dormant_refused_when_storage_sentinel_absent(
    g1_registry_isolation, tmp_path, monkeypatch, emitted
):
    from claude_bundles import cdp_registry as reg
    from claude_bundles.cdp_registry_store import load_active

    exc = _storage_unavailable_class()
    sentinel = tmp_path / "sentinel-present"
    sentinel.write_text("ready\n")
    monkeypatch.setenv("ULG_BROWSER_STORAGE_SENTINEL", str(sentinel))
    launches = []

    def _launch(port, profile):
        launches.append(port)
        profile.mkdir(parents=True, exist_ok=True)
        return 4242

    seat = reg.register_lane(
        holder="v1-dormant",
        purpose="operator-proxy",
        launch_chrome=_launch,
        is_listening=lambda _p: False,
    )
    reg.bind_session_address(seat.registration_id, chat_url="https://claude.ai/cowork/cse_v1_3b")
    reg._release_driver_lock(seat.registration_id)
    assert reg.make_dormant(seat.registration_id, is_listening=lambda _p: False) is not None
    assert len(launches) == 1
    assert _storage_callers(emitted) == []

    sentinel.unlink()
    before = load_active()
    with pytest.raises(exc):
        reg.relaunch_dormant(
            seat.registration_id,
            launch_chrome=_launch,
            is_listening=lambda _p: False,
        )
    assert len(launches) == 1
    assert load_active() == before
    assert load_active()[seat.registration_id]["status"] == "dormant"
    assert _storage_callers(emitted) == ["reserve_allocating_row"]


def test_v1_5_storage_guard_off_is_base_behaviour(g1_registry_isolation, monkeypatch):
    monkeypatch.delenv("ULG_BROWSER_STORAGE_SENTINEL", raising=False)
    from claude_bundles.cdp_registry import hygiene as reg
    from claude_bundles.cdp_registry import lifecycle

    lifecycle.register_lane(holder="pin1", purpose="ask", is_listening=lambda _p: False, launch=False)
    reg.hygiene_reclaim_extended(is_listening=lambda _p: False, empty_trash=False)
