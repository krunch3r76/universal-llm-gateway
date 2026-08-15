"""Unit tests for mission-operator attended CSE resolver (AC1–AC3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from claude_bundles.cdp_orphans import LivePort

from cdp_ask.attended_dormant import DormantCandidate
from cdp_ask.attended_operator import (
    AttendedResolveDormant,
    AttendedResolveRefused,
    AttendedResolveSuccess,
    build_shadow_urls,
    dormant_to_http_body,
    refused_http_status,
    resolve_attended_operator,
)

pytestmark = pytest.mark.offline

CSE_U = "https://claude.ai/cowork/cse_mission123"


@dataclass(frozen=True)
class _FakeReg:
    registration_id: str
    port: int
    profile_suffix: str
    profile: Path
    cdp_url: str
    holder: str
    purpose: str | None = None
    parent_thread: str | None = None
    mission_kind: str | None = None
    started_at: float | None = None


def _reg(
    reg_id: str,
    *,
    purpose: str = "operator-proxy",
    port: int = 9223,
    parent_thread: str | None = None,
    mission_kind: str | None = None,
) -> _FakeReg:
    return _FakeReg(
        registration_id=reg_id,
        port=port,
        profile_suffix="s",
        profile=Path("/tmp/p"),
        cdp_url=f"http://127.0.0.1:{port}",
        holder="holder-a",
        purpose=purpose,
        parent_thread=parent_thread,
        mission_kind=mission_kind,
    )


def test_ac1_one_mission_registration_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = _reg("reg-1")
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_registry.list_active",
        lambda: [reg],
    )
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_U if rid == "reg-1" else None,
    )
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_orphans.probe_live_ports",
        lambda port_range=None: [
            LivePort(
                port=9223,
                profile=None,
                page_urls=(CSE_U,),
                has_live_cse=True,
            )
        ],
    )
    outcome = resolve_attended_operator()
    assert isinstance(outcome, AttendedResolveSuccess)
    assert outcome.registration_id == "reg-1"
    assert outcome.cdp_url == "http://127.0.0.1:9223"
    assert outcome.chat_url == CSE_U
    assert outcome.purpose == "operator-proxy"
    assert outcome.source == "cse-session-registry"
    assert outcome.probe.live is True


def test_ac1_two_mission_registrations_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_registry.list_active",
        lambda: [_reg("reg-1"), _reg("reg-2", port=9224, purpose="mission")],
    )
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_U if rid in {"reg-1", "reg-2"} else None,
    )
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_orphans.probe_live_ports",
        lambda port_range=None: [],
    )
    outcome = resolve_attended_operator()
    assert isinstance(outcome, AttendedResolveRefused)
    assert outcome.code == "ambiguous_attended"
    assert outcome.candidates is not None
    assert len(outcome.candidates) == 2
    assert refused_http_status(outcome.code) == 409


def test_same_lane_extras_collapse_to_one_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _reg("reg-hop", port=9230, parent_thread="6655", mission_kind="hop")
    extra = _reg("reg-old", port=9228, parent_thread="6655", mission_kind="root")
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_registry.list_active",
        lambda: [extra, holder],
    )
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_U if rid == "reg-hop" else f"{CSE_U}-{rid}",
    )
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_orphans.probe_live_ports",
        lambda port_range=None: [
            LivePort(
                port=9230,
                profile=None,
                page_urls=(CSE_U,),
                has_live_cse=True,
            )
        ],
    )
    outcome = resolve_attended_operator()
    assert isinstance(outcome, AttendedResolveSuccess)
    assert outcome.registration_id == "reg-hop"
    assert outcome.chat_url == CSE_U


def test_ac1_zero_mission_registrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_registry.list_active",
        lambda: [_reg("reg-1", purpose="ask")],
    )
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_registry.chat_url_for_registration",
        lambda rid: None,
    )
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_orphans.probe_live_ports",
        lambda port_range=None: [],
    )
    outcome = resolve_attended_operator()
    assert isinstance(outcome, AttendedResolveRefused)
    assert outcome.code == "no_attended_cse"
    assert refused_http_status(outcome.code) == 404


def test_ac2_shadow_urls_unregistered_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = _reg("reg-1", port=9223)
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_registry.list_active",
        lambda: [reg],
    )
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_U if rid == "reg-1" else None,
    )
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_orphans.probe_live_ports",
        lambda port_range=None: [
            LivePort(port=9223, profile=None, page_urls=(CSE_U,), has_live_cse=True),
            LivePort(port=9227, profile=None, page_urls=(CSE_U,), has_live_cse=True),
        ],
    )
    outcome = resolve_attended_operator()
    assert isinstance(outcome, AttendedResolveSuccess)
    assert outcome.cdp_url == "http://127.0.0.1:9223"
    assert any(s["chat_url"] == CSE_U and 9227 in s["ports_seen"] for s in outcome.shadow_urls)


def test_ac3_sole_candidate_liveness_failed_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = _reg("reg-1", port=9223)
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_registry.list_active",
        lambda: [reg],
    )
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_registry.chat_url_for_registration",
        lambda rid: CSE_U if rid == "reg-1" else None,
    )
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_orphans.probe_live_ports",
        lambda port_range=None: [
            LivePort(port=9223, profile=None, page_urls=(), has_live_cse=False),
            LivePort(port=9227, profile=None, page_urls=(CSE_U,), has_live_cse=True),
        ],
    )
    outcome = resolve_attended_operator()
    assert isinstance(outcome, AttendedResolveRefused)
    assert outcome.code == "attended_liveness_failed"
    assert outcome.candidate is not None
    assert outcome.candidate["cdp_url"] == "http://127.0.0.1:9223"
    assert outcome.probe is not None
    assert outcome.probe.live is False
    assert refused_http_status(outcome.code) == 424


def _no_live_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_registry.list_active", lambda: []
    )
    monkeypatch.setattr(
        "cdp_ask.attended_operator.cdp_orphans.probe_live_ports",
        lambda port_range=None: [],
    )


def _dormant(reg_id: str, url: str, *, dormant_at: float = 100.0) -> DormantCandidate:
    return DormantCandidate(
        registration_id=reg_id,
        chat_url=url,
        purpose="operator-proxy",
        dormant_at=dormant_at,
    )


def test_dormant_seat_resolves_as_attended_not_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parked host is attended by URL: an open tab is not what confers attendance."""
    _no_live_hosts(monkeypatch)
    monkeypatch.setattr(
        "cdp_ask.attended_operator.dormant_candidates",
        lambda: [_dormant("reg-parked", CSE_U)],
    )

    outcome = resolve_attended_operator()

    assert isinstance(outcome, AttendedResolveDormant)
    assert outcome.registration_id == "reg-parked"
    assert outcome.chat_url == CSE_U
    assert outcome.reattachable is True

    body = dormant_to_http_body(outcome)
    assert body["cdp_url"] is None
    assert body["dormant"] is True
    assert body["probe"]["live"] is False


def test_two_dormant_seats_refuse_as_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_live_hosts(monkeypatch)
    monkeypatch.setattr(
        "cdp_ask.attended_operator.dormant_candidates",
        lambda: [
            _dormant("reg-a", CSE_U),
            _dormant("reg-b", "https://claude.ai/cowork/cse_other"),
        ],
    )

    outcome = resolve_attended_operator()

    assert isinstance(outcome, AttendedResolveRefused)
    assert outcome.code == "ambiguous_attended"
    assert outcome.candidates is not None
    assert [c["cdp_url"] for c in outcome.candidates] == [None, None]


def test_no_hosts_and_no_dormant_seats_still_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_live_hosts(monkeypatch)
    monkeypatch.setattr("cdp_ask.attended_operator.dormant_candidates", lambda: [])

    outcome = resolve_attended_operator()

    assert isinstance(outcome, AttendedResolveRefused)
    assert outcome.code == "no_attended_cse"


def test_build_shadow_urls_dedupes_ports() -> None:
    from cdp_ask.attended_operator import AttendedCandidate

    candidates = [
        AttendedCandidate(
            registration_id="r1",
            cdp_url="http://127.0.0.1:9223",
            chat_url=CSE_U,
            purpose="operator-proxy",
        )
    ]
    live_ports = [
        LivePort(port=9227, profile=None, page_urls=(CSE_U,), has_live_cse=True),
        LivePort(port=9228, profile=None, page_urls=(CSE_U,), has_live_cse=True),
    ]
    shadows = build_shadow_urls(candidates, live_ports=live_ports)
    entry = next(s for s in shadows if s["chat_url"] == CSE_U)
    assert entry["ports_seen"] == [9227, 9228]
