"""Unit tests for mission-operator attended CSE resolver (AC1–AC3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from claude_bundles.cdp_orphans import LivePort

from cdp_ask.attended_operator import (
    AttendedResolveRefused,
    AttendedResolveSuccess,
    build_shadow_urls,
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


def _reg(
    reg_id: str,
    *,
    purpose: str = "operator-proxy",
    port: int = 9223,
) -> _FakeReg:
    return _FakeReg(
        registration_id=reg_id,
        port=port,
        profile_suffix="s",
        profile=Path("/tmp/p"),
        cdp_url=f"http://127.0.0.1:{port}",
        holder="holder-a",
        purpose=purpose,
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
