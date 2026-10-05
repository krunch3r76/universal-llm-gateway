"""Hermetic lane-current CSE selection (friction 37834 specimen)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from cdp_ask.lane_current_cse import resolve_lane_current_cse, select_lane_current

pytestmark = pytest.mark.offline

LANE = "12286"
LIVE = "https://claude.ai/cowork/cse_01XLJF9g6JFdHYnrDSePwc23"
DRAIN = "https://claude.ai/cowork/cse_017cw5A7geCQNPPP7LzB78vB"
UNCLAIMED = "https://claude.ai/cowork/cse_01KKeJtTGiUH32mi3wk7dvZx"


def _pages() -> list[tuple[int, str, str | None]]:
    return [
        (9223, LIVE, "ws://9223"),
        (9224, DRAIN, "ws://9224"),
        (9225, UNCLAIMED, "ws://9225"),
    ]


def _probe(port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
    if port == 9223:
        return {"streaming": True, "stop": False, "tool_pause": False}, True
    if port == 9224:
        return {"streaming": False, "stop": False, "tool_pause": False}, True
    return {"streaming": False, "stop": False, "tool_pause": False}, True


def _prov(url: str) -> dict[str, Any] | None:
    if url == LIVE:
        return {
            "parent_thread_claim": LANE,
            "reason": "idle_exit",
            "registration_id": "fc69582c",
        }
    if url == DRAIN:
        return {
            "parent_thread_claim": LANE,
            "reason": "hygiene_drain",
            "registration_id": "drain-reg",
        }
    return None


def _resolve(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "snap": {},
        "list_pages": lambda: iter(_pages()),
        "probe_page": _probe,
        "provenance_for": _prov,
        "list_active": lambda: [],
        "chat_url_for_registration": lambda _rid: None,
        "purpose_for_registration": lambda _rid: "operator-proxy",
        "now": lambda: 1_700_000_000.0,
    }
    kwargs.update(overrides)
    return resolve_lane_current_cse(LANE, **kwargs)


def test_specimen_in_flight_current_and_unclaimed() -> None:
    class _Live:
        parent_thread = LANE
        registration_id = "fc69582c"
        purpose = "operator-proxy"

    body = _resolve(
        list_active=lambda: [_Live()],
        chat_url_for_registration=lambda rid: LIVE if rid == "fc69582c" else None,
    )
    assert body["state"] == "current"
    assert body["basis"] == "in_flight"
    assert body["current"]["chat_url"] == LIVE
    assert body["current"]["in_flight"] is True
    assert body["current"]["ports"] == [9223]
    assert "registry_row" in body["current"]["claims"]
    assert body["current"]["evidence_class"] == "live_binding"
    assert body["current"]["provenance_reason"] == "idle_exit"
    assert [page["chat_url"] for page in body["stale"]] == [DRAIN]
    assert body["stale"][0]["stale_reason"] == "not_in_flight"
    assert body["unclaimed_cse_urls"] == [UNCLAIMED]
    assert body["candidates"] == []
    assert body["source"] == "cdp_live_probe"
    assert body["scope"] == f"parent_thread:{LANE}"


def test_two_in_flight_ambiguous() -> None:
    def probe(port: int, ws: str) -> tuple[dict[str, Any] | None, bool]:
        if port in {9223, 9224}:
            return {"streaming": True, "stop": False, "tool_pause": False}, True
        return _probe(port, ws)

    body = _resolve(probe_page=probe)
    assert body["state"] == "ambiguous"
    assert body["reason"] == "multiple_in_flight"
    assert body["current"] is None
    assert {page["chat_url"] for page in body["candidates"]} == {LIVE, DRAIN}


def test_holder_page_live_when_none_in_flight() -> None:
    def probe(_port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        return {"streaming": False, "stop": False, "tool_pause": False}, True

    snap = {
        "seat_rows": [
            {
                "registration_id": "held",
                "parent_thread": LANE,
                "purpose": "operator-proxy",
                "seat_bound_at": 1.0,
                "chat_url": DRAIN,
            }
        ]
    }
    class _Held:
        parent_thread = LANE
        registration_id = "held"
        purpose = "operator-proxy"

    body = _resolve(
        snap=snap,
        probe_page=probe,
        list_active=lambda: [_Held()],
        chat_url_for_registration=lambda rid: DRAIN if rid == "held" else None,
    )
    assert body["state"] == "current"
    assert body["basis"] == "seat_holder"
    assert body["current"]["chat_url"] == DRAIN
    assert body["current"]["evidence_class"] == "live_binding"
    assert body["stale"][0]["chat_url"] == LIVE
    assert body["stale"][0]["stale_reason"] == "not_seat_holder"
    assert body["seat_holder"]["registration_id"] == "held"
    assert body["seat_holder"]["evidence_class"] == "live_binding"


def test_one_claimed_idle_is_not_current() -> None:
    def pages():
        yield 9224, DRAIN, "ws://9224"
        yield 9225, UNCLAIMED, "ws://9225"

    def probe(_port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        return {"streaming": False, "stop": False, "tool_pause": False}, True

    body = _resolve(list_pages=pages, probe_page=probe)
    assert body["state"] == "ambiguous"
    assert body["reason"] == "no_live_signal"
    assert body["current"] is None
    assert [page["chat_url"] for page in body["candidates"]] == [DRAIN]


def test_consult_purpose_streaming_page_is_unclaimed() -> None:
    class _Consult:
        parent_thread = LANE
        registration_id = "consult-reg"
        purpose = "consult"

    def prov(url: str) -> dict[str, Any] | None:
        if url == LIVE:
            return {"parent_thread_claim": LANE, "registration_id": "consult-reg"}
        return None

    class _Held:
        parent_thread = LANE
        registration_id = "held"
        purpose = "operator-proxy"

    snap = {
        "seat_rows": [
            {
                "registration_id": "held",
                "parent_thread": LANE,
                "purpose": "operator-proxy",
                "seat_bound_at": 1.0,
            }
        ]
    }
    body = _resolve(
        snap=snap,
        provenance_for=prov,
        list_active=lambda: [_Consult(), _Held()],
        chat_url_for_registration=lambda rid: {
            "consult-reg": LIVE,
            "held": UNCLAIMED,
        }.get(rid),
        purpose_for_registration=lambda rid: (
            "consult" if rid == "consult-reg" else "operator-proxy"
        ),
    )
    assert body["state"] == "current"
    assert body["basis"] == "seat_holder"
    assert body["current"]["chat_url"] == UNCLAIMED
    assert body["current"]["evidence_class"] == "live_binding"
    assert LIVE in body["unclaimed_cse_urls"]


def test_provenance_without_known_purpose_is_unclaimed() -> None:
    body = _resolve(purpose_for_registration=lambda _rid: None)
    assert body["state"] == "none"
    assert body["unclaimed_cse_urls"] == sorted([LIVE, DRAIN, UNCLAIMED])


def test_two_claimed_idle_no_holder_is_no_live_signal() -> None:
    def probe(_port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        return {"streaming": False, "stop": False, "tool_pause": False}, True

    body = _resolve(probe_page=probe)
    assert body["state"] == "ambiguous"
    assert body["reason"] == "no_live_signal"
    assert body["current"] is None


def test_probe_failure_plus_one_in_flight_is_incomplete() -> None:
    def probe(port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        if port == 9223:
            return {"streaming": True, "stop": False, "tool_pause": False}, True
        return None, False

    body = _resolve(probe_page=probe)
    assert body["state"] == "ambiguous"
    assert body["reason"] == "probe_incomplete"
    drain = next(page for page in body["candidates"] if page["chat_url"] == DRAIN)
    assert drain["in_flight"] is None
    assert drain["probe_ok"] is False


def test_zero_claimed_is_none() -> None:
    def prov(_url: str) -> None:
        return None

    body = _resolve(provenance_for=prov)
    assert body["state"] == "none"
    assert body["reason"] == "no_claimed_live_page"
    assert body["unclaimed_cse_urls"] == sorted([LIVE, DRAIN, UNCLAIMED])
    assert body["current"] is None


def test_list_pages_and_provenance_raising_do_not_escape() -> None:
    def boom_pages():
        raise RuntimeError("list down")
        yield 0, "", None

    def boom_prov(_url: str) -> dict[str, Any]:
        raise RuntimeError("prov down")

    body = resolve_lane_current_cse(
        LANE,
        snap={},
        list_pages=boom_pages,
        provenance_for=boom_prov,
        list_active=lambda: [],
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 10.0,
    )
    assert body["state"] == "none"

    def pages():
        yield 9223, LIVE, "ws://x"
        raise RuntimeError("mid")

    body = resolve_lane_current_cse(
        LANE,
        snap={},
        list_pages=pages,
        provenance_for=boom_prov,
        list_active=lambda: [],
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 10.0,
    )
    assert body["state"] == "none"
    assert body["reason"] == "no_claimed_live_page"
    assert body["unclaimed_cse_urls"] == [LIVE]


def test_unreadable_registry_is_probe_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom_active() -> list[Any]:
        raise RuntimeError("active")

    body = _resolve(list_active=boom_active)
    assert body["state"] == "none"
    assert body["reason"] == "probe_error"

    def boom_load() -> dict[str, Any]:
        raise RuntimeError("active.json corrupt")

    monkeypatch.setattr("claude_bundles.cdp_registry_store.load_active", boom_load)
    body = _resolve(purpose_for_registration=None)
    assert body["reason"] == "probe_error"


def test_unknown_registry_page_off_holder_outranks_holder() -> None:
    class _Live:
        parent_thread = LANE
        registration_id = "fc69582c"
        purpose = "operator-proxy"

    def probe(port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        if port == 9223:
            return None, False
        return {"streaming": False, "stop": False, "tool_pause": False}, True

    snap = {
        "seat_rows": [
            {
                "registration_id": "held",
                "parent_thread": LANE,
                "purpose": "operator-proxy",
                "seat_bound_at": 1.0,
            }
        ]
    }
    body = _resolve(
        snap=snap,
        probe_page=probe,
        list_active=lambda: [_Live()],
        chat_url_for_registration=lambda rid: {
            "fc69582c": LIVE,
            "held": UNCLAIMED,
        }.get(rid),
    )
    assert body["state"] == "ambiguous"
    assert body["reason"] == "probe_incomplete"
    live = next(page for page in body["candidates"] if page["chat_url"] == LIVE)
    assert live["in_flight"] is None
    assert "registry_row" in live["claims"]


def test_unknown_provenance_only_page_does_not_block_holder() -> None:
    def probe(port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        if port == 9224:
            return None, False
        return {"streaming": False, "stop": False, "tool_pause": False}, True

    snap = {
        "seat_rows": [
            {
                "registration_id": "held",
                "parent_thread": LANE,
                "purpose": "operator-proxy",
                "seat_bound_at": 1.0,
            }
        ]
    }
    class _Held:
        parent_thread = LANE
        registration_id = "held"
        purpose = "operator-proxy"

    body = _resolve(
        snap=snap,
        probe_page=probe,
        list_active=lambda: [_Held()],
        chat_url_for_registration=lambda rid: UNCLAIMED if rid == "held" else None,
    )
    assert body["state"] == "current"
    assert body["basis"] == "seat_holder"
    assert body["current"]["chat_url"] == UNCLAIMED
    assert body["current"]["evidence_class"] == "live_binding"


def test_top_level_failure_is_probe_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr("cdp_ask.lane_current_cse._resolve", explode)
    failed = resolve_lane_current_cse(
        LANE,
        snap={},
        purpose_for_registration=lambda _rid: None,
        now=lambda: 10.0,
    )
    assert failed["state"] == "none"
    assert failed["reason"] == "probe_error"


def test_same_url_two_ports_is_one_identity() -> None:
    def pages():
        yield 9223, LIVE, "ws://a"
        yield 9226, LIVE + "/", "ws://b"

    def prov(url: str) -> dict[str, Any] | None:
        if url.rstrip("/").endswith("01XLJF9g6JFdHYnrDSePwc23"):
            return {
                "parent_thread_claim": LANE,
                "reason": "idle_exit",
                "registration_id": "fc69582c",
            }
        return None

    def probe(_port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        return {"streaming": True, "stop": False, "tool_pause": False}, True

    class _Live:
        parent_thread = LANE
        registration_id = "fc69582c"
        purpose = "operator-proxy"

    body = resolve_lane_current_cse(
        LANE,
        snap={},
        list_pages=pages,
        probe_page=probe,
        provenance_for=prov,
        list_active=lambda: [_Live()],
        chat_url_for_registration=lambda rid: LIVE if rid == "fc69582c" else None,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 10.0,
    )
    assert body["state"] == "current"
    assert body["current"]["ports"] == [9223, 9226]
    assert body["current"]["chat_url"] == LIVE
    assert body["current"]["evidence_class"] == "live_binding"


def test_registry_row_only_claim_counts() -> None:
    class _Reg:
        parent_thread = LANE
        registration_id = "reg-only"
        purpose = "operator-proxy"

    def prov(_url: str) -> None:
        return None

    def pages():
        yield 9224, DRAIN, "ws://d"

    def probe(_port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        return {"streaming": True, "stop": False, "tool_pause": False}, True

    body = resolve_lane_current_cse(
        LANE,
        snap={},
        list_pages=pages,
        probe_page=probe,
        provenance_for=prov,
        list_active=lambda: [_Reg()],
        chat_url_for_registration=lambda rid: DRAIN if rid == "reg-only" else None,
        now=lambda: 10.0,
    )
    assert body["state"] == "current"
    assert body["basis"] == "in_flight"
    assert body["current"]["claims"] == ["registry_row"]
    assert body["current"]["registration_ids"] == ["reg-only"]


def test_select_helper_multiple_in_flight() -> None:
    pages = [
        {"chat_url": "a", "in_flight": True},
        {"chat_url": "b", "in_flight": True},
    ]
    state, basis, reason, current = select_lane_current(pages, None)
    assert (state, basis, reason, current) == (
        "ambiguous",
        None,
        "multiple_in_flight",
        None,
    )


@pytest.mark.offline
def test_dormant_holder_blocks_sole_idle_shadow() -> None:
    shadow = "https://claude.ai/cowork/cse_017cw5A7geCQNPPP7LzB78vB"
    holder = "https://claude.ai/cowork/cse_01KKeJtTGiUH32mi3wk7dvZx"
    pages = [
        {
            "chat_url": shadow,
            "in_flight": False,
            "claims": ["provenance_claim"],
            "evidence_class": "stored_association",
        }
    ]
    state, basis, reason, current = select_lane_current(
        pages, holder, holder_live=False
    )
    assert (state, basis, reason, current) == (
        "ambiguous",
        None,
        "seat_holder_not_open",
        None,
    )


def test_attended_route_parent_thread_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    from cdp_ask.app import create_app

    calls: list[tuple[str, dict | None]] = []

    def fake(lane: str, *, snap: dict | None) -> dict[str, Any]:
        calls.append((lane, snap))
        if lane == "cur":
            return {
                "parent_thread": lane,
                "state": "current",
                "basis": "in_flight",
                "reason": None,
                "current": {"chat_url": LIVE},
                "stale": [],
                "candidates": [],
                "seat_holder": None,
                "unclaimed_cse_urls": [],
                "as_of": "t",
                "source": "cdp_live_probe",
                "scope": f"parent_thread:{lane}",
            }
        if lane == "amb":
            return {
                "parent_thread": lane,
                "state": "ambiguous",
                "basis": None,
                "reason": "multiple_in_flight",
                "current": None,
                "stale": [],
                "candidates": [{"chat_url": LIVE}],
                "seat_holder": None,
                "unclaimed_cse_urls": [],
                "as_of": "t",
                "source": "cdp_live_probe",
                "scope": f"parent_thread:{lane}",
            }
        return {
            "parent_thread": lane,
            "state": "none",
            "basis": None,
            "reason": "probe_error" if lane == "err" else "no_claimed_live_page",
            "current": None,
            "stale": [],
            "candidates": [],
            "seat_holder": None,
            "unclaimed_cse_urls": [],
            "as_of": "t",
            "source": "cdp_live_probe",
            "scope": f"parent_thread:{lane}",
        }

    monkeypatch.setattr("cdp_ask.lane_current_cse.resolve_lane_current_cse", fake)
    monkeypatch.setattr("cdp_ask.followup_events.emit", lambda _event: None)
    app = create_app()
    client = TestClient(app)
    ok = client.get(
        "/v1/project-ask/attended-operator", params={"parent_thread": "cur"}
    )
    amb = client.get(
        "/v1/project-ask/attended-operator", params={"parent_thread": "amb"}
    )
    none = client.get(
        "/v1/project-ask/attended-operator", params={"parent_thread": "none"}
    )
    assert ok.status_code == 200
    assert ok.json()["current"]["chat_url"] == LIVE
    assert amb.status_code == 409
    assert amb.json()["code"] == "lane_cse_ambiguous"
    err = client.get(
        "/v1/project-ask/attended-operator", params={"parent_thread": "err"}
    )
    assert none.status_code == 404
    assert none.json()["code"] == "lane_cse_none"
    assert err.status_code == 503
    assert err.json()["code"] == "lane_cse_probe_error"
    assert [item[0] for item in calls] == ["cur", "amb", "none", "err"]


KKEJ = "https://claude.ai/cowork/cse_01KKeJtTGiUH32mi3wk7dvZx"
XLJF9 = LIVE
BB6A = DRAIN


def _a37868_pages() -> list[tuple[int, str, str | None]]:
    return [
        (9230, KKEJ, "ws://9230"),
        (9223, XLJF9, "ws://9223"),
        (9224, BB6A, "ws://9224"),
    ]


def _a37868_probe(port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
    if port == 9230:
        return {"streaming": True, "stop": False, "tool_pause": False}, True
    return {"streaming": False, "stop": False, "tool_pause": False}, True


def _a37868_prov(url: str) -> dict[str, Any] | None:
    if url == KKEJ:
        return {
            "parent_thread_claim": LANE,
            "reason": "hygiene_drain",
            "registration_id": "drain-a",
        }
    if url == XLJF9:
        return {
            "parent_thread_claim": LANE,
            "reason": "hygiene_drain",
            "registration_id": "seat-x",
        }
    return None


def _a37868_snap() -> dict[str, Any]:
    return {
        "seat_rows": [
            {
                "registration_id": "seat-x",
                "parent_thread": LANE,
                "purpose": "operator-proxy",
                "seat_bound_at": 1.0,
                "chat_url": XLJF9,
            }
        ]
    }


def test_a37868_drain_stream_is_not_current() -> None:
    body = resolve_lane_current_cse(
        LANE,
        snap=_a37868_snap(),
        list_pages=lambda: iter(_a37868_pages()),
        probe_page=_a37868_probe,
        provenance_for=_a37868_prov,
        list_active=lambda: [],
        chat_url_for_registration=lambda rid: XLJF9 if rid == "seat-x" else None,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
    )
    assert body["state"] == "ambiguous"
    assert body["reason"] == "stored_association_streaming"
    assert body["current"] is None
    kkej = next(p for p in body["candidates"] if p["chat_url"] == KKEJ)
    assert kkej["evidence_class"] == "stored_association"
    assert BB6A in body["unclaimed_cse_urls"]
    assert body["seat_holder"]["evidence_class"] == "stored_association"


def test_a37868_drain_stream_live_holder_still_not_current() -> None:
    class _SeatX:
        parent_thread = LANE
        registration_id = "seat-x"
        purpose = "operator-proxy"

    body = resolve_lane_current_cse(
        LANE,
        snap=_a37868_snap(),
        list_pages=lambda: iter(_a37868_pages()),
        probe_page=_a37868_probe,
        provenance_for=_a37868_prov,
        list_active=lambda: [_SeatX()],
        chat_url_for_registration=lambda rid: XLJF9 if rid == "seat-x" else None,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
    )
    assert body["state"] == "ambiguous"
    assert body["reason"] == "stored_association_streaming"
    assert body["current"] is None


def test_live_registry_stream_is_current_despite_drain_reason() -> None:
    class _Op:
        parent_thread = LANE
        registration_id = "live-op"
        purpose = "operator-proxy"

    def pages():
        yield 9223, LIVE, "ws://9223"

    def probe(port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        return {"streaming": True, "stop": False, "tool_pause": False}, True

    def prov(url: str) -> dict[str, Any] | None:
        if url == LIVE:
            return {
                "parent_thread_claim": LANE,
                "reason": "hygiene_drain",
                "registration_id": "live-op",
            }
        return None

    body = resolve_lane_current_cse(
        LANE,
        snap={},
        list_pages=pages,
        probe_page=probe,
        provenance_for=prov,
        list_active=lambda: [_Op()],
        chat_url_for_registration=lambda rid: LIVE if rid == "live-op" else None,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
    )
    assert body["state"] == "current"
    assert body["basis"] == "in_flight"
    assert body["current"]["evidence_class"] == "live_binding"
    assert body["current"]["provenance_reason"] == "hygiene_drain"


def test_dormant_holder_open_idle_is_not_current() -> None:
    holder_url = DRAIN

    def pages():
        yield 9224, holder_url, "ws://9224"

    def probe(_port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        return {"streaming": False, "stop": False, "tool_pause": False}, True

    def prov(url: str) -> dict[str, Any] | None:
        if url == holder_url:
            return {
                "parent_thread_claim": LANE,
                "reason": "hygiene_drain",
                "registration_id": "seat-x",
            }
        return None

    snap = {
        "seat_rows": [
            {
                "registration_id": "seat-x",
                "parent_thread": LANE,
                "purpose": "operator-proxy",
                "seat_bound_at": 1.0,
                "chat_url": holder_url,
            }
        ]
    }
    body = resolve_lane_current_cse(
        LANE,
        snap=snap,
        list_pages=pages,
        probe_page=probe,
        provenance_for=prov,
        list_active=lambda: [],
        chat_url_for_registration=lambda rid: holder_url if rid == "seat-x" else None,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
    )
    assert body["state"] == "ambiguous"
    assert body["reason"] == "seat_holder_dormant"
    assert body["current"] is None


@pytest.mark.parametrize(
    "kwargs,expect_current",
    [
        (
            {
                "list_pages": lambda: iter([(9223, LIVE, "ws://9223")]),
                "probe_page": lambda p, _w: (
                    ({"streaming": True, "stop": False, "tool_pause": False}, True)
                    if p == 9223
                    else (None, False)
                ),
                "provenance_for": lambda u: (
                    {
                        "parent_thread_claim": LANE,
                        "reason": "hygiene_drain",
                        "registration_id": "x",
                    }
                    if u == LIVE
                    else None
                ),
                "list_active": lambda: [],
            },
            False,
        ),
        (
            {
                "snap": {
                    "seat_rows": [
                        {
                            "registration_id": "h",
                            "parent_thread": LANE,
                            "purpose": "operator-proxy",
                            "seat_bound_at": 1.0,
                            "chat_url": DRAIN,
                        }
                    ]
                },
                "list_pages": lambda: iter([(9224, DRAIN, "ws://9224")]),
                "probe_page": lambda _p, _w: (
                    {"streaming": False, "stop": False, "tool_pause": False},
                    True,
                ),
                "provenance_for": lambda u: (
                    {
                        "parent_thread_claim": LANE,
                        "registration_id": "h",
                    }
                    if u == DRAIN
                    else None
                ),
                "list_active": lambda: [],
                "chat_url_for_registration": lambda rid: (
                    DRAIN if rid == "h" else None
                ),
            },
            False,
        ),
        (
            {
                "list_pages": lambda: iter([(9224, DRAIN, "ws://9224")]),
                "probe_page": lambda _p, _w: (
                    {"streaming": True, "stop": False, "tool_pause": False},
                    True,
                ),
                "provenance_for": lambda _u: None,
                "list_active": lambda: [
                    type(
                        "R",
                        (),
                        {
                            "parent_thread": LANE,
                            "registration_id": "reg-only",
                            "purpose": "operator-proxy",
                        },
                    )()
                ],
                "chat_url_for_registration": lambda rid: (
                    DRAIN if rid == "reg-only" else None
                ),
            },
            True,
        ),
        (
            {
                "list_pages": lambda: iter([(9224, DRAIN, "ws://9224")]),
                "probe_page": lambda _p, _w: (
                    {"streaming": False, "stop": False, "tool_pause": False},
                    True,
                ),
                "provenance_for": lambda _u: None,
                "list_active": lambda: [
                    type(
                        "R",
                        (),
                        {
                            "parent_thread": LANE,
                            "registration_id": "reg-only",
                            "purpose": "operator-proxy",
                        },
                    )()
                ],
                "chat_url_for_registration": lambda rid: (
                    DRAIN if rid == "reg-only" else None
                ),
                "snap": {
                    "seat_rows": [
                        {
                            "registration_id": "reg-only",
                            "parent_thread": LANE,
                            "purpose": "operator-proxy",
                            "seat_bound_at": 1.0,
                        }
                    ]
                },
            },
            True,
        ),
        (
            {
                "list_pages": lambda: iter([(9223, LIVE, "ws://9223")]),
                "probe_page": lambda _p, _w: (
                    {"streaming": True, "stop": False, "tool_pause": False},
                    True,
                ),
                "provenance_for": lambda u: (
                    {
                        "parent_thread_claim": LANE,
                        "registration_id": "x",
                    }
                    if u == LIVE
                    else None
                ),
                "list_active": lambda: [],
                "snap": {
                    "seat_rows": [
                        {
                            "registration_id": "live-h",
                            "parent_thread": LANE,
                            "purpose": "operator-proxy",
                            "seat_bound_at": 1.0,
                            "chat_url": DRAIN,
                        }
                    ]
                },
                "chat_url_for_registration": lambda rid: (
                    DRAIN if rid == "live-h" else None
                ),
            },
            False,
        ),
        (
            {
                "list_pages": lambda: iter([(9224, DRAIN, "ws://9224")]),
                "probe_page": lambda _p, _w: (
                    {"streaming": False, "stop": False, "tool_pause": False},
                    True,
                ),
                "provenance_for": lambda u: (
                    {
                        "parent_thread_claim": LANE,
                        "registration_id": "live-h",
                    }
                    if u == DRAIN
                    else None
                ),
                "list_active": lambda: [
                    type(
                        "R",
                        (),
                        {
                            "parent_thread": LANE,
                            "registration_id": "live-h",
                            "purpose": "operator-proxy",
                        },
                    )()
                ],
                "chat_url_for_registration": lambda rid: (
                    DRAIN if rid == "live-h" else None
                ),
                "snap": {
                    "seat_rows": [
                        {
                            "registration_id": "live-h",
                            "parent_thread": LANE,
                            "purpose": "operator-proxy",
                            "seat_bound_at": 1.0,
                            "chat_url": DRAIN,
                        }
                    ]
                },
            },
            True,
        ),
    ],
)
def test_current_is_always_live_binding(kwargs: dict, expect_current: bool) -> None:
    base: dict[str, Any] = {
        "snap": kwargs.get("snap", {}),
        "list_pages": kwargs["list_pages"],
        "probe_page": kwargs["probe_page"],
        "provenance_for": kwargs["provenance_for"],
        "list_active": kwargs["list_active"],
        "chat_url_for_registration": kwargs.get(
            "chat_url_for_registration", lambda _rid: None
        ),
        "purpose_for_registration": lambda _rid: "operator-proxy",
        "now": lambda: 1_700_000_000.0,
    }
    body = resolve_lane_current_cse(LANE, **base)
    current = body.get("current")
    assert body["state"] != "current" or (
        isinstance(current, dict) and current.get("evidence_class") == "live_binding"
    )
    if expect_current:
        assert body["state"] == "current"


def test_attended_route_blank_parent_thread_uses_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.app import create_app
    from cdp_ask.attended_operator import AttendedResolveSuccess, LivenessProbe

    def boom(*_a, **_k):
        raise AssertionError("lane probe must not run")

    monkeypatch.setattr("cdp_ask.lane_current_cse.resolve_lane_current_cse", boom)
    monkeypatch.setattr("cdp_ask.followup_events.emit", lambda _event: None)
    monkeypatch.setattr(
        "cdp_ask.app.resolve_attended_operator",
        lambda: AttendedResolveSuccess(
            registration_id="reg-a",
            cdp_url="http://127.0.0.1:9223",
            chat_url=LIVE,
            purpose="operator-proxy",
            probe=LivenessProbe(live=True, checked_at=1.0),
            source="registry",
            shadow_urls=[],
        ),
    )
    client = TestClient(create_app())
    blank = client.get(
        "/v1/project-ask/attended-operator", params={"parent_thread": "  "}
    )
    absent = client.get("/v1/project-ask/attended-operator")
    assert blank.status_code == 200
    assert absent.status_code == 200
    assert absent.json()["registration_id"] == "reg-a"
    assert "code" not in absent.json() or absent.json().get("state") != "none"
