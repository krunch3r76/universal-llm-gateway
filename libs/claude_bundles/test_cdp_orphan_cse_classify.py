"""Hermetic tests for CSE closable/protected classification (S1 emit-only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles import cdp_orphan_cse_classify as classify
from claude_bundles.cse_url import normalize_cse_url

pytestmark = pytest.mark.offline

_CSE_URL = "https://claude.ai/cowork/cse_abc123"
_WS = "ws://127.0.0.1:9229/devtools/page/1"


@pytest.fixture(autouse=True)
def _clear_idle_cache() -> None:
    classify._idle_since.clear()
    yield
    classify._idle_since.clear()


def test_attach_resolved_via_chat_url_is_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = {"url": _CSE_URL, "id": "p1", "webSocketDebuggerUrl": _WS}
    index = {normalize_cse_url(_CSE_URL): "reg-chat"}
    target = classify.classify_cse_target(
        page,
        port=9229,
        profile=Path("/tmp/claude-ai-chrome-profile-reg-x"),
        chat_url_index=index,
        running_registration_ids=set(),
        now=1000.0,
    )
    assert target.classification == "protected"
    assert target.attach_resolution == "chat_url"
    assert target.attach_registration_id == "reg-chat"


def test_idle_probe_unavailable_is_protected_fail_closed() -> None:
    page = {"url": _CSE_URL, "id": "p1"}
    target = classify.classify_cse_target(
        page,
        port=9229,
        profile=None,
        chat_url_index={},
        running_registration_ids=set(),
        now=1000.0,
    )
    assert target.classification == "protected"
    assert target.idle_probe_ok is False
    assert "idle_probe_unavailable" in target.classification_reason


def test_streaming_is_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        classify,
        "probe_page_liveness_sync",
        lambda _port, _ws: ({"streaming": True, "stop": False, "tool_pause": False}, True),
    )
    page = {"url": _CSE_URL, "id": "p1", "webSocketDebuggerUrl": _WS}
    target = classify.classify_cse_target(
        page,
        port=9229,
        profile=None,
        chat_url_index={},
        running_registration_ids=set(),
        now=1000.0,
    )
    assert target.classification == "protected"
    assert target.in_flight is True


def test_idle_past_dwell_is_closable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(classify, "cse_idle_dwell_s", lambda: 60.0)
    monkeypatch.setattr(
        classify,
        "probe_page_liveness_sync",
        lambda _port, _ws: ({"streaming": False, "stop": False, "tool_pause": False}, True),
    )
    page = {"url": _CSE_URL, "id": "p1", "webSocketDebuggerUrl": _WS}
    key = (9229, normalize_cse_url(_CSE_URL))
    classify._idle_since[key] = 900.0
    target = classify.classify_cse_target(
        page,
        port=9229,
        profile=None,
        chat_url_index={},
        running_registration_ids=set(),
        now=1000.0,
    )
    assert target.classification == "closable"
    assert target.idle_dwell_s == pytest.approx(100.0)


def test_by_id_refuse_protects_operator_seat_cse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """6893 safety bind: cse_016UZLY… protected in classify code, not prose."""
    from claude_bundles import cdp_orphans
    from claude_bundles.cdp_reclaim_refuse import guard_cse_reclaim

    protected_url = "https://claude.ai/cowork/cse_016UZLY1LHLTyTQG7dAH1eW8"
    monkeypatch.setattr(classify, "cse_idle_dwell_s", lambda: 60.0)
    monkeypatch.setattr(
        classify,
        "probe_page_liveness_sync",
        lambda _port, _ws: ({"streaming": False, "stop": False, "tool_pause": False}, True),
    )
    page = {"url": protected_url, "id": "p1", "webSocketDebuggerUrl": _WS}
    key = (9247, normalize_cse_url(protected_url))
    classify._idle_since[key] = 900.0
    target = classify.classify_cse_target(
        page,
        port=9247,
        profile=None,
        chat_url_index={},
        running_registration_ids=set(),
        now=1000.0,
    )
    assert target.classification == "protected"
    assert target.classification_reason == (
        "by_id_refuse:cse_016UZLY1LHLTyTQG7dAH1eW8"
    )
    assert guard_cse_reclaim(protected_url) == (
        "by_id_refuse:cse_016UZLY1LHLTyTQG7dAH1eW8"
    )
    actuator = cdp_orphans.attempt_reclaim_cse_target(target)
    assert actuator["reclaimed"] is False
    assert actuator["reason"] == "by_id_refuse:cse_016UZLY1LHLTyTQG7dAH1eW8"


def test_probe_evaluate_failed_is_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        classify,
        "probe_page_liveness_sync",
        lambda _port, _ws: (None, False),
    )
    page = {"url": _CSE_URL, "id": "p1", "webSocketDebuggerUrl": _WS}
    target = classify.classify_cse_target(
        page,
        port=9229,
        profile=None,
        chat_url_index={},
        running_registration_ids=set(),
        now=1000.0,
    )
    assert target.classification == "protected"
    assert target.classification_reason == "idle_probe_unavailable:evaluate_failed"


def test_orphan_scan_as_dict_surfaces_closable_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles import cdp_orphans

    reg_profile = tmp_path / "claude-ai-chrome-profile-reg-deadbeef"
    closable = classify.CseTarget(
        url=_CSE_URL,
        target_id="p1",
        classification="closable",
        attach_resolution=None,
        attach_registration_id=None,
        idle_probe_ok=True,
        in_flight=False,
        idle_dwell_s=400.0,
        classification_reason="test",
    )
    orphan = cdp_orphans.Orphan(
        port=9229,
        pid=1,
        profile=reg_profile,
        has_live_cse=True,
        uptime_s=10.0,
        cse_targets=(closable,),
    )
    scan = cdp_orphans.OrphanScanResult(
        matched=(orphan,),
        rejected=(),
        unevaluable=(),
        ports_live=1,
        ports_skipped_registered=0,
    )
    payload = cdp_orphans.orphan_scan_as_dict(scan)
    assert payload["closable_count"] == 1
    assert payload["protected_count"] == 0
    assert payload["reclaim_enabled"] is False
    assert payload["cse_classification"] == "scan_ephemeral"
    assert payload["matched"][0]["cse_targets"][0]["classification"] == "closable"
