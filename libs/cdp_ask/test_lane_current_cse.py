"""Hermetic lane-current CSE selection (friction 37834 specimen)."""

from __future__ import annotations

import json
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
    """A still-open seat whose probe failed blocks the holder.

    ``seat_lane`` is set so the row stays a live registry claim. An unstamped
    historical row beside an open seat is superseded and must not block.
    """

    class _Live:
        parent_thread = LANE
        registration_id = "fc69582c"
        purpose = "operator-proxy"
        seat_lane = LANE
        seat_closed_at = None

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
def test_listable_holder_absent_page_is_stored_association() -> None:
    """Holder registration is listable, but its page is not an open candidate."""
    holder = "https://claude.ai/cowork/cse_01KKeJtTGiUH32mi3wk7dvZx"

    class _Holder:
        parent_thread = LANE
        registration_id = "holder-reg"
        purpose = "operator-proxy"

    body = resolve_lane_current_cse(
        LANE,
        snap={
            "seat_rows": [
                {
                    "registration_id": "holder-reg",
                    "parent_thread": LANE,
                    "purpose": "operator-proxy",
                    "seat_bound_at": 1.0,
                    "chat_url": holder,
                }
            ]
        },
        list_pages=lambda: iter([(9223, LIVE, "ws://9223")]),
        probe_page=lambda _port, _ws: (
            {"streaming": False, "stop": False, "tool_pause": False},
            True,
        ),
        provenance_for=lambda url: (
            {
                "parent_thread_claim": LANE,
                "reason": "idle_exit",
                "registration_id": "other",
            }
            if url == LIVE
            else None
        ),
        list_active=lambda: [_Holder()],
        chat_url_for_registration=lambda rid: holder if rid == "holder-reg" else None,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 10.0,
    )
    assert body["seat_holder"]["evidence_class"] == "stored_association"
    assert body["seat_holder"]["registration_id"] == "holder-reg"


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
    assert kkej["in_flight"] is True
    assert "provenance_claim" in kkej["claims"]
    if "evidence_class" in kkej:
        assert kkej["evidence_class"] == "stored_association"
    assert _wire409(body) < 1024
    assert BB6A in body["unclaimed_cse_urls"] or body.get("unclaimed_cse_count") == 1
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


# a:38216 — same seat as an in-flight generate, idle after harvest.
A38216_URL = "https://claude.ai/cowork/cse_01XDUWbh3t7aShgbefu12M6R"
A38216_REG = "d6a4cdc38e16405dbaf92ee90f882d94"


def test_a38216_idle_attended_after_harvest_is_live_binding() -> None:
    """Open idle holder after a terminal generate is current, not stored association.

    Breaks when the tracked generate is still in flight (streaming) — that path
    stays basis in_flight — and when provenance is hygiene_drain.
    """

    def pages():
        yield 9226, A38216_URL, "ws://9226"

    def probe(_port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        return {"streaming": False, "stop": False, "tool_pause": False}, True

    snap = {
        "seat_rows": [
            {
                "registration_id": A38216_REG,
                "parent_thread": LANE,
                "purpose": "operator-proxy",
                "seat_bound_at": 1.0,
                "chat_url": A38216_URL,
            }
        ],
        "rows": [
            {
                "registration_id": A38216_REG,
                "execution_id": "hop-harvest",
                "status": "completed",
                "stream_state": "terminal:hop-harvest",
                "parent_thread": LANE,
            }
        ],
    }
    body = resolve_lane_current_cse(
        LANE,
        snap=snap,
        list_pages=pages,
        probe_page=probe,
        provenance_for=lambda url: (
            {
                "parent_thread_claim": LANE,
                "reason": "idle_exit",
                "registration_id": A38216_REG,
            }
            if url == A38216_URL
            else None
        ),
        list_active=lambda: [],
        chat_url_for_registration=lambda rid: A38216_URL if rid == A38216_REG else None,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
    )
    assert body["state"] == "current"
    assert body["basis"] == "attended_idle"
    assert body["current"]["chat_url"] == A38216_URL
    assert body["current"]["evidence_class"] == "live_binding"
    assert body["seat_holder"]["evidence_class"] == "live_binding"
    assert body["seat_holder"]["registration_id"] == A38216_REG


def test_a38228_idle_without_admission_rows_is_live_binding() -> None:
    """Production active-work ``rows`` omit completed generates.

    Breaks when idle resolve still requires a terminal admission row: the
    attested tab stays ``seat_holder_dormant`` / ``stored_association`` and
    identity-less followup never sees ``live_binding``.
    """
    snap = {
        "rows": [],
        "seat_rows": [
            {
                "registration_id": A38216_REG,
                "parent_thread": LANE,
                "purpose": "operator-proxy",
                "seat_bound_at": 1.0,
            }
        ],
    }
    body = resolve_lane_current_cse(
        LANE,
        snap=snap,
        list_pages=lambda: iter([(9226, A38216_URL, "ws://9226")]),
        probe_page=lambda _p, _w: (
            {"streaming": False, "stop": False, "tool_pause": False},
            True,
        ),
        provenance_for=lambda url: (
            {
                "parent_thread_claim": LANE,
                "reason": "idle_exit",
                "registration_id": A38216_REG,
            }
            if url == A38216_URL
            else None
        ),
        list_active=lambda: [],
        chat_url_for_registration=lambda rid: A38216_URL if rid == A38216_REG else None,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
    )
    assert body["state"] == "current"
    assert body["basis"] == "attended_idle"
    assert body["current"]["evidence_class"] == "live_binding"
    assert body["current"]["chat_url"] == A38216_URL
    assert body["seat_holder"]["evidence_class"] == "live_binding"


def test_a38228_identity_less_followup_binds_idle_page() -> None:
    """Identity-less followup copies the idle live page; it does not ask for chat_url."""
    from cdp_ask.followup_resolve import _lane_seat_followup_gate
    from cdp_ask.models import FollowupProjectAskRequest

    snap = {
        "rows": [],
        "observed_at": "t",
        "seat_rows": [
            {
                "registration_id": A38216_REG,
                "parent_thread": LANE,
                "purpose": "operator-proxy",
                "seat_bound_at": 1.0,
                "source": "registry",
            }
        ],
    }
    body = resolve_lane_current_cse(
        LANE,
        snap=snap,
        list_pages=lambda: iter([(9226, A38216_URL, "ws://9226")]),
        probe_page=lambda _p, _w: (
            {"streaming": False, "stop": False, "tool_pause": False},
            True,
        ),
        provenance_for=lambda url: (
            {
                "parent_thread_claim": LANE,
                "reason": "idle_exit",
                "registration_id": A38216_REG,
            }
            if url == A38216_URL
            else None
        ),
        list_active=lambda: [],
        chat_url_for_registration=lambda rid: A38216_URL if rid == A38216_REG else None,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
    )
    assert body["current"]["evidence_class"] == "live_binding"

    req = FollowupProjectAskRequest(prompt_text="memo", parent_thread=LANE)
    from unittest.mock import patch

    with patch(
        "cdp_ask.followup_resolve.resolve_operator_seat",
        return_value={
            "authority_reachable": True,
            "registration_id": A38216_REG,
            "chat_url": A38216_URL,
        },
    ):
        bound, err, _path = _lane_seat_followup_gate(
            req,
            {"body": body},
            snap,
        )
    assert err is None
    assert bound.chat_url == A38216_URL
    assert bound.registration_id == A38216_REG


def test_a38216_hygiene_drain_stays_refused_after_harvest() -> None:
    snap = {
        "seat_rows": [
            {
                "registration_id": A38216_REG,
                "parent_thread": LANE,
                "purpose": "operator-proxy",
                "seat_bound_at": 1.0,
                "chat_url": A38216_URL,
            }
        ],
        "rows": [
            {
                "registration_id": A38216_REG,
                "status": "completed",
                "stream_state": "terminal:hop-harvest",
            }
        ],
    }
    body = resolve_lane_current_cse(
        LANE,
        snap=snap,
        list_pages=lambda: iter([(9226, A38216_URL, "ws://9226")]),
        probe_page=lambda _p, _w: (
            {"streaming": False, "stop": False, "tool_pause": False},
            True,
        ),
        provenance_for=lambda url: (
            {
                "parent_thread_claim": LANE,
                "reason": "hygiene_drain",
                "registration_id": A38216_REG,
            }
            if url == A38216_URL
            else None
        ),
        list_active=lambda: [],
        chat_url_for_registration=lambda rid: A38216_URL if rid == A38216_REG else None,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
    )
    assert body["state"] == "ambiguous"
    assert body["reason"] == "seat_holder_dormant"
    assert body["current"] is None


def test_a38216_closed_page_stays_unclaimed() -> None:
    """Holder URL absent from the open probe stays none (closed page)."""
    snap = {
        "seat_rows": [
            {
                "registration_id": A38216_REG,
                "parent_thread": LANE,
                "purpose": "operator-proxy",
                "seat_bound_at": 1.0,
                "chat_url": A38216_URL,
            }
        ],
        "rows": [
            {
                "registration_id": A38216_REG,
                "status": "completed",
                "stream_state": "terminal:hop-harvest",
            }
        ],
    }
    body = resolve_lane_current_cse(
        LANE,
        snap=snap,
        list_pages=lambda: iter([]),
        probe_page=lambda _p, _w: (None, False),
        provenance_for=lambda _url: None,
        list_active=lambda: [],
        chat_url_for_registration=lambda rid: A38216_URL if rid == A38216_REG else None,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
    )
    assert body["state"] == "stored"
    assert body["basis"] == "stored_seat_holder"
    assert body["state"] != "current"
    assert body["reason"] == "seat_holder_stored"
    assert body["seat_holder"]["evidence_class"] == "stored_association"


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
                "chat_url_for_registration": lambda rid: DRAIN if rid == "h" else None,
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


# Plan r4 truncates live ids. These fixtures keep the cited prefixes and
# complete them to 32-hex registration ids / a full cowork URL.
_OPEN_0611 = "42e8a8e3000000000000000000000001"
_OPEN_0650 = "0ee03e6d000000000000000000000002"
_URL_0650 = "https://claude.ai/cowork/cse_019S45specimen0650holder"
_SEAT_SPECIMENS = (
    {
        "label": "0547",
        "open_rid": "4f7b570f66b24c71959222d0a93d9286",
        "open_url": "https://claude.ai/cowork/cse_successor_0547",
        "closed_rid": "c4c8f4f1a5ba4ea99684182e34fb052f",
        "closed_url": "https://claude.ai/cowork/cse_predecessor_0547",
    },
    {
        "label": "0611",
        "open_rid": _OPEN_0611,
        "open_url": "https://claude.ai/cowork/cse_01LcNK85yiXs9f3b9m1NxCjo",
        "closed_rid": "c016a6ts000000000000000000000011",
        "closed_url": "https://claude.ai/cowork/cse_016a6TsJzteXZJeTENuTsgxx",
    },
    {
        "label": "0650",
        "open_rid": _OPEN_0650,
        "open_url": _URL_0650,
        "closed_rid": _OPEN_0611,
        "closed_url": "https://claude.ai/cowork/cse_01LcNK85yiXs9f3b9m1NxCjo",
    },
)


class _SeatReg:
    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


def _streaming() -> tuple[dict[str, Any], bool]:
    return {"streaming": True, "stop": False, "tool_pause": False}, True


def _idle() -> tuple[dict[str, Any], bool]:
    return {"streaming": False, "stop": False, "tool_pause": False}, True


def _specimen_registry(spec: dict[str, str]) -> tuple[list[_SeatReg], dict[str, str]]:
    """Open seat, closed predecessor, and 68 closed or unstamped lane rows."""
    rows: list[_SeatReg] = [
        _SeatReg(
            parent_thread=LANE,
            registration_id=spec["open_rid"],
            purpose="operator-proxy",
            seat_lane=LANE,
            seat_closed_at=None,
            seat_bound_at=300.0,
            started_at=300.0,
        ),
        _SeatReg(
            parent_thread=LANE,
            registration_id=spec["closed_rid"],
            purpose="operator-proxy",
            seat_lane=LANE,
            seat_closed_at=200.0,
            seat_bound_at=200.0,
            started_at=200.0,
        ),
    ]
    urls = {
        spec["open_rid"]: spec["open_url"],
        spec["closed_rid"]: spec["closed_url"],
    }
    for index in range(68):
        rid = f"filler{index:04d}0000000000000000000000"
        url = f"https://claude.ai/cowork/cse_filler_{index:02d}"
        if index < 34:
            rows.append(
                _SeatReg(
                    parent_thread=LANE,
                    registration_id=rid,
                    purpose="operator-proxy",
                    seat_lane=LANE,
                    seat_closed_at=100.0,
                    seat_bound_at=float(index),
                    started_at=float(index),
                )
            )
        else:
            rows.append(
                _SeatReg(
                    parent_thread=LANE,
                    registration_id=rid,
                    purpose="operator-proxy",
                    seat_lane=None,
                    seat_closed_at=None,
                    seat_bound_at=None,
                    started_at=float(index),
                )
            )
        urls[rid] = url
    return rows, urls


def _resolve_specimen(spec: dict[str, str]) -> dict[str, Any]:
    rows, urls = _specimen_registry(spec)
    open_port = 9400
    closed_port = 9401
    unknown_ports = set(range(9402, 9419))

    def pages():
        yield open_port, spec["open_url"], "ws://open"
        yield closed_port, spec["closed_url"], "ws://closed"
        for index in range(68):
            port = 9402 + index
            url = f"https://claude.ai/cowork/cse_filler_{index:02d}"
            ws = None if port in unknown_ports else f"ws://{port}"
            yield port, url, ws

    def probe(port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        if port in {open_port, closed_port}:
            return _streaming()
        if port in unknown_ports:
            return None, False
        return _idle()

    snap = {
        "seat_rows": [
            {
                "registration_id": spec["open_rid"],
                "parent_thread": LANE,
                "seat_lane": LANE,
                "purpose": "operator-proxy",
                "seat_bound_at": 300.0,
                "chat_url": spec["open_url"],
            }
        ]
    }
    return resolve_lane_current_cse(
        LANE,
        snap=snap,
        list_pages=pages,
        probe_page=probe,
        provenance_for=lambda _url: None,
        list_active=lambda: rows,
        chat_url_for_registration=lambda rid: urls.get(rid),
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
    )


def _assert_open_seat_current(body: dict[str, Any], spec: dict[str, str]) -> None:
    assert body["state"] == "current"
    assert body["basis"] == "in_flight"
    assert body["reason"] is None
    assert body["candidates"] == []
    assert body["current"]["chat_url"] == spec["open_url"]
    assert body["current"]["registration_ids"] == [spec["open_rid"]]
    assert body["current"]["evidence_class"] == "live_binding"
    stale_urls = {page["chat_url"] for page in body["stale"]}
    assert spec["closed_url"] in stale_urls
    assert spec["open_url"] not in stale_urls
    assert len(body["stale"]) == 69
    for page in body["stale"]:
        assert page["evidence_class"] == "stored_association"
        assert "superseded_registry_row" in page["claims"]
        assert "registry_row" not in page["claims"]
        assert page["stale_reason"] == "superseded_seat"


@pytest.mark.parametrize("spec", _SEAT_SPECIMENS, ids=lambda spec: spec["label"])
def test_two_in_flight_prefers_open_seat_over_superseded(spec: dict[str, str]) -> None:
    """Closed predecessor stays in_flight; the open seat is still current."""
    _assert_open_seat_current(_resolve_specimen(spec), spec)


@pytest.mark.parametrize("spec", _SEAT_SPECIMENS, ids=lambda spec: spec["label"])
def test_superseded_and_unstamped_rows_do_not_collide_with_open_seat(
    spec: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """~70 registry rows, including unstamped legacy rows, leave one current seat."""
    _assert_open_seat_current(_resolve_specimen(spec), spec)
    if spec["label"] != "0547":
        return
    from claude_bundles.cdp_registry.models import _row_to_registration
    from claude_bundles.cdp_registry.session_address import current_seat_for_lane

    projected = _row_to_registration(
        {
            "registration_id": "4f7b570f66b24c71959222d0a93d9286",
            "port": 9223,
            "profile_suffix": "seat-axis",
            "holder": "holder",
            "seat_lane": "12286",
            "seat_bound_at": 11.0,
            "seat_closed_at": 1700000000.0,
            "seat_close_reason": "superseded",
            "superseded_by": "4f7b570f66b24c71959222d0a93d9286",
            "started_at": 10.0,
            "execution_id": "exec-seat",
        }
    )
    assert projected.seat_closed_at == 1700000000.0
    assert projected.seat_lane == "12286"
    assert projected.superseded_by == "4f7b570f66b24c71959222d0a93d9286"
    assert projected.started_at == 10.0
    assert projected.execution_id == "exec-seat"
    assert projected.seat_bound_at == 11.0
    assert projected.seat_close_reason == "superseded"

    def _row(rid: str, *, bound: float, closed: float | None, started: float) -> dict:
        return {
            "registration_id": rid,
            "port": 9300,
            "profile_suffix": rid,
            "holder": "holder",
            "status": "active",
            "parent_thread": "12286",
            "purpose": "operator-proxy",
            "seat_lane": "12286",
            "seat_bound_at": bound,
            "seat_closed_at": closed,
            "started_at": started,
        }

    monkeypatch.setattr(
        "claude_bundles.cdp_registry.session_address._store.load_active",
        lambda: {
            "older": _row("older", bound=1.0, closed=None, started=1.0),
            "newer": _row("newer", bound=5.0, closed=None, started=2.0),
            "shut": _row("shut", bound=9.0, closed=9.0, started=3.0),
        },
    )
    winner = current_seat_for_lane("12286")
    assert winner is not None
    assert winner.registration_id == "newer"
    assert current_seat_for_lane("other") is None


def _cse_url(n: int) -> str:
    return f"https://claude.ai/cowork/cse_wirecap{n:04d}"


def _reg_row(
    rid: str,
    *,
    closed: bool = False,
    url: str | None = None,
) -> Any:
    class _Row:
        parent_thread = LANE
        registration_id = rid
        purpose = "operator-proxy"
        seat_lane = LANE
        seat_closed_at = 1.0 if closed else None

    _Row.__name__ = f"_Reg_{rid}"
    return _Row()


def _wire409(body: dict[str, Any]) -> int:
    return len(json.dumps({**body, "code": "lane_cse_ambiguous"}).encode("utf-8"))


def test_l12_superseded_count_and_wire_cap_with_unclaimed() -> None:
    superseded = [_reg_row(f"sup-{i:02d}", closed=True) for i in range(20)]
    active_urls = [_cse_url(1), _cse_url(2)]
    unclaimed = [_cse_url(100 + i) for i in range(15)]

    def list_pages() -> Any:
        ports = 9200
        for url in active_urls + unclaimed:
            ports += 1
            yield ports, url, f"ws://{ports}"

    reg_urls = {f"act-{i}": active_urls[i] for i in range(2)}

    class _Active:
        pass

    active_regs = [_reg_row("act-0"), _reg_row("act-1")] + superseded

    def chat_url_for_registration(rid: str) -> str | None:
        if rid in reg_urls:
            return reg_urls[rid]
        return None

    def probe(port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        return {"streaming": True, "stop": False, "tool_pause": False}, True

    body = resolve_lane_current_cse(
        LANE,
        snap={},
        list_pages=list_pages,
        probe_page=probe,
        provenance_for=lambda _u: None,
        list_active=lambda: active_regs,
        chat_url_for_registration=chat_url_for_registration,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
    )
    assert body["state"] == "ambiguous"
    assert body["superseded_count"] == 20
    for page in body["candidates"]:
        assert "in_flight" in page
        assert "claims" in page
        assert "superseded_registry_row" not in (page.get("claims") or [])
    assert _wire409(body) < 1024


def test_l12_many_active_candidates_truncated_under_wire_cap() -> None:
    active_urls = [_cse_url(i) for i in range(25)]
    unclaimed = [_cse_url(500 + i) for i in range(15)]
    active_regs = [_reg_row(f"live-{i:02d}") for i in range(25)]

    def list_pages() -> Any:
        for i, url in enumerate(active_urls + unclaimed):
            yield 9300 + i, url, f"ws://{9300 + i}"

    def chat_url_for_registration(rid: str) -> str | None:
        idx = int(rid.split("-")[1])
        return active_urls[idx]

    body = resolve_lane_current_cse(
        LANE,
        snap={},
        list_pages=list_pages,
        probe_page=lambda _p, _w: (
            {"streaming": True, "stop": False, "tool_pause": False},
            True,
        ),
        provenance_for=lambda _u: None,
        list_active=lambda: active_regs,
        chat_url_for_registration=chat_url_for_registration,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
    )
    assert body["state"] == "ambiguous"
    assert body.get("candidates_truncated") is True
    for page in body["candidates"]:
        assert "in_flight" in page
        assert "claims" in page
    assert _wire409(body) < 1024


def test_l12_include_superseded_returns_all_claimed() -> None:
    active_urls = [_cse_url(1), _cse_url(2)]
    superseded_url = _cse_url(99)

    def list_pages() -> Any:
        yield 9220, active_urls[0], "ws://9220"
        yield 9221, active_urls[1], "ws://9221"
        yield 9222, superseded_url, "ws://9222"

    regs = [
        _reg_row("live-a"),
        _reg_row("live-b"),
        _reg_row("old-seat", closed=True),
    ]

    def chat_url_for_registration(rid: str) -> str | None:
        return {
            "live-a": active_urls[0],
            "live-b": active_urls[1],
            "old-seat": superseded_url,
        }.get(rid)

    body = resolve_lane_current_cse(
        LANE,
        snap={},
        list_pages=list_pages,
        probe_page=lambda _p, _w: (
            {"streaming": True, "stop": False, "tool_pause": False},
            True,
        ),
        provenance_for=lambda _u: None,
        list_active=lambda: regs,
        chat_url_for_registration=chat_url_for_registration,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
        include_superseded=True,
    )
    assert body["state"] == "ambiguous"
    urls = {p["chat_url"] for p in body["candidates"]}
    assert superseded_url in urls
    assert len(body["candidates"]) == 3


def test_l12_followup_gate_superseded_count_and_wire_cap() -> None:
    from cdp_ask.followup_resolve import _lane_seat_followup_gate
    from cdp_ask.models import FollowupProjectAskRequest

    superseded = [_reg_row(f"sup-{i:02d}", closed=True) for i in range(20)]
    u1, u2 = _cse_url(1), _cse_url(2)

    def list_pages() -> Any:
        yield 9223, u1, "ws://9223"
        yield 9224, u2, "ws://9224"

    active_regs = [_reg_row("act-0"), _reg_row("act-1")] + superseded

    def chat_url_for_registration(rid: str) -> str | None:
        return {"act-0": u1, "act-1": u2}.get(rid)

    body = resolve_lane_current_cse(
        LANE,
        snap={},
        list_pages=list_pages,
        probe_page=lambda _p, _w: (
            {"streaming": True, "stop": False, "tool_pause": False},
            True,
        ),
        provenance_for=lambda _u: None,
        list_active=lambda: active_regs,
        chat_url_for_registration=chat_url_for_registration,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
    )
    req = FollowupProjectAskRequest(prompt_text="memo", parent_thread=LANE)
    _bound, err, _path = _lane_seat_followup_gate(req, {"body": body}, {})
    assert err is not None
    assert err.error == "lane_cse_ambiguous"
    assert err.superseded_count == 20
    assert len(json.dumps(err.model_dump()).encode("utf-8")) < 1024


def test_l12_refused_to_http_body_ambiguous_attended_wire_cap() -> None:
    from cdp_ask.attended_operator import AttendedResolveRefused, refused_to_http_body

    candidates = [
        {
            "registration_id": f"reg-{i:02d}",
            "chat_url": _cse_url(i),
            "purpose": "operator-proxy",
            "provenance": {"parent_thread_claim": LANE, "reason": "x" * 200},
        }
        for i in range(20)
    ]
    shadow = [_cse_url(800 + i) for i in range(15)]
    body = refused_to_http_body(
        AttendedResolveRefused(
            code="ambiguous_attended",
            candidates=candidates,
            shadow_urls=shadow,
        )
    )
    assert len(json.dumps(body).encode("utf-8")) < 1024
