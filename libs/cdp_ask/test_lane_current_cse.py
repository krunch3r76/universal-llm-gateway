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
        "now": lambda: 1_700_000_000.0,
    }
    kwargs.update(overrides)
    return resolve_lane_current_cse(LANE, **kwargs)


def test_specimen_in_flight_current_and_unclaimed() -> None:
    body = _resolve()
    assert body["state"] == "current"
    assert body["basis"] == "in_flight"
    assert body["current"]["chat_url"] == LIVE
    assert body["current"]["in_flight"] is True
    assert body["current"]["ports"] == [9223]
    assert "provenance_claim" in body["current"]["claims"]
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
    body = _resolve(
        snap=snap,
        probe_page=probe,
        chat_url_for_registration=lambda rid: DRAIN if rid == "held" else None,
    )
    assert body["state"] == "current"
    assert body["basis"] == "seat_holder"
    assert body["current"]["chat_url"] == DRAIN
    assert body["stale"][0]["chat_url"] == LIVE
    assert body["stale"][0]["stale_reason"] == "not_seat_holder"
    assert body["seat_holder"]["registration_id"] == "held"


def test_one_claimed_idle_is_sole_live_claim() -> None:
    def pages():
        yield 9224, DRAIN, "ws://9224"
        yield 9225, UNCLAIMED, "ws://9225"

    def probe(_port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        return {"streaming": False, "stop": False, "tool_pause": False}, True

    body = _resolve(list_pages=pages, probe_page=probe)
    assert body["state"] == "current"
    assert body["basis"] == "sole_live_claim"
    assert body["current"]["chat_url"] == DRAIN
    assert body["stale"] == []


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
        now=lambda: 10.0,
    )
    assert body["state"] == "none"

    def pages():
        yield 9223, LIVE, "ws://x"
        raise RuntimeError("mid")

    def boom_active() -> list[Any]:
        raise RuntimeError("active")

    body = resolve_lane_current_cse(
        LANE,
        snap={},
        list_pages=pages,
        provenance_for=boom_prov,
        list_active=boom_active,
        now=lambda: 10.0,
    )
    assert body["state"] in {"none", "current", "ambiguous"}


def test_same_url_two_ports_is_one_identity() -> None:
    def pages():
        yield 9223, LIVE, "ws://a"
        yield 9226, LIVE + "/", "ws://b"

    def prov(url: str) -> dict[str, Any] | None:
        if url.rstrip("/").endswith("01XLJF9g6JFdHYnrDSePwc23"):
            return {"parent_thread_claim": LANE, "reason": "idle_exit"}
        return None

    def probe(_port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        return {"streaming": True, "stop": False, "tool_pause": False}, True

    body = resolve_lane_current_cse(
        LANE,
        snap={},
        list_pages=pages,
        probe_page=probe,
        provenance_for=prov,
        list_active=lambda: [],
        now=lambda: 10.0,
    )
    assert body["state"] == "current"
    assert body["current"]["ports"] == [9223, 9226]
    assert body["current"]["chat_url"] == LIVE


def test_registry_row_only_claim_counts() -> None:
    class _Reg:
        parent_thread = LANE
        registration_id = "reg-only"

    def prov(_url: str) -> None:
        return None

    def pages():
        yield 9224, DRAIN, "ws://d"

    def probe(_port: int, _ws: str) -> tuple[dict[str, Any] | None, bool]:
        return {"streaming": False, "stop": False, "tool_pause": False}, True

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
    assert body["basis"] == "sole_live_claim"
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
            "reason": "no_claimed_live_page",
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
    assert none.status_code == 404
    assert none.json()["code"] == "lane_cse_none"
    assert [item[0] for item in calls] == ["cur", "amb", "none"]


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
