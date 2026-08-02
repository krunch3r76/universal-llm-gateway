"""Arm-predicate + lane-tag tests for agent_bus.request (MCP package).

The worker transport (``httpx``) lives in ``request_worker_client`` after the
SLOC split, so liveness patches target that module; ``request`` re-exports the
names it calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tools.agent_bus.request import (
    _merge_lane_tags,
    _request_impl,
    probe_auto_liveness,
)


def test_merge_lane_tags_dedupes():
    assert _merge_lane_tags(["lane:cursor-auto", "x"]) == [
        "lane:cursor-auto",
        "x",
        "bus_lifecycle:persistent",
    ]
    merged = _merge_lane_tags(None)
    assert "lane:cursor-auto" in merged
    assert "bus_lifecycle:persistent" in merged
    assert "lane:life-to-code" not in _merge_lane_tags(["foo"])


def test_arm_predicate_no_live_handler():
    """F1: unreachable / non-live ⇒ live=False (never auto-admit-armed)."""
    with (
        patch("tools.agent_bus.request_worker_client.httpx.Client") as client_cls,
        patch("tools.agent_bus.request_worker_client.time.sleep"),
    ):
        client = client_cls.return_value.__enter__.return_value
        client.get.side_effect = OSError("down")
        result = probe_auto_liveness()
    assert result["live"] is False
    assert result["attempts"] == 3
    assert "error_class" in result
    assert "elapsed_s" in result


def test_arm_predicate_live_true():
    with patch("tools.agent_bus.request_worker_client.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        resp = client.get.return_value
        resp.status_code = 200
        resp.json.return_value = {"live": True, "handler_count": 1}
        result = probe_auto_liveness()
    assert result["live"] is True
    assert result["attempts"] == 1


def test_arm_predicate_http_error_not_armed():
    with (
        patch("tools.agent_bus.request_worker_client.httpx.Client") as client_cls,
        patch("tools.agent_bus.request_worker_client.time.sleep"),
    ):
        client = client_cls.return_value.__enter__.return_value
        resp = client.get.return_value
        resp.status_code = 503
        result = probe_auto_liveness()
    assert result["live"] is False
    assert result["error_class"] == "http_5xx"
    assert client.get.call_count == 3


def test_liveness_retries_transient_then_live():
    """T1: transport-unknown retries then succeeds."""
    with (
        patch("tools.agent_bus.request_worker_client.httpx.Client") as client_cls,
        patch("tools.agent_bus.request_worker_client.time.sleep"),
    ):
        client = client_cls.return_value.__enter__.return_value
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"live": True, "handler_count": 1}
        client.get.side_effect = [OSError("down"), ok_resp]
        result = probe_auto_liveness()
    assert result["live"] is True
    assert client.get.call_count == 2
    assert result["attempts"] == 2


def test_liveness_no_live_handler_zero_retries():
    """T3: definitive dead handler ⇒ single probe, no backoff."""
    with (
        patch("tools.agent_bus.request_worker_client.httpx.Client") as client_cls,
        patch("tools.agent_bus.request_worker_client.time.sleep") as sleep_mock,
    ):
        client = client_cls.return_value.__enter__.return_value
        resp = client.get.return_value
        resp.status_code = 200
        resp.json.return_value = {"live": False, "handler_count": 0}
        result = probe_auto_liveness()
    assert result["live"] is False
    assert result["reason"] == "no_live_handler"
    assert result["attempts"] == 1
    assert result["error_class"] == "handler_dead"
    assert client.get.call_count == 1
    sleep_mock.assert_not_called()


def test_request_probe_exhaustion_visible_park():
    """T2: probe exhaustion exposes enqueue_failure + annotated poll_hint."""
    send_payload = {
        "send_path": "new_thread",
        "thread": {"id": "77", "slug": "park"},
        "turn": {"id": 1, "thread": "77", "turn_number": 1},
    }
    liveness_exhausted = {
        "live": False,
        "reason": "liveness_unreachable",
        "error": "down",
        "attempts": 3,
        "elapsed_s": 4.2,
        "error_class": "connect_refused",
    }
    with (
        patch("tools.agent_bus.request._send_dispatch", return_value=send_payload),
        patch(
            "tools.agent_bus.request.probe_auto_liveness",
            return_value=liveness_exhausted,
        ),
        patch("tools.agent_bus.request.record") as record_mock,
    ):
        result = _request_impl(
            new_slug="park",
            thread=None,
            to="cursor",
            subject="DIRECTIVE",
            body="TYPE: DIRECTIVE",
            from_agent="web-anthropic",
            tags=None,
            sidecar_content=None,
            sidecar_slug=None,
            desired_model="auto",
            desired_effort="medium",
            contract="answer",
            require_attended=False,
            request_id="req-park",
            after_turn=0,
            summary=None,
        )
    assert result["thread"]["id"] == "77"
    assert result["turn"]["turn_number"] == 1
    assert result["enqueue_failure"]["terminal_park"] is True
    assert result["poll_hint"]["producer"] == "none"
    record_mock.assert_called_once_with(
        "mcp.agentbus.request.degraded",
        thread="77",
        turn_number=1,
        reason="liveness_unreachable",
        error_class="connect_refused",
        elapsed_s=4.2,
        attempts=3,
    )


def test_request_enqueue_failure_visible_park():
    """T4: live probe + failed enqueue ⇒ same visibility rule as probe degrade."""
    send_payload = {
        "send_path": "new_thread",
        "thread": {"id": "88", "slug": "enq-fail"},
        "turn": {"id": 1, "thread": "88", "turn_number": 1},
    }
    with (
        patch("tools.agent_bus.request._send_dispatch", return_value=send_payload),
        patch(
            "tools.agent_bus.request.probe_auto_liveness",
            return_value={
                "live": True,
                "reason": "ok",
                "attempts": 1,
                "elapsed_s": 0.1,
                "error_class": "unknown",
            },
        ),
        patch(
            "tools.agent_bus.request.enqueue_auto_job",
            return_value={
                "ok": False,
                "handler_status": "no-auto-handler",
                "reason": "enqueue_unreachable",
                "error": "timeout",
            },
        ),
        patch("tools.agent_bus.request.record") as record_mock,
    ):
        result = _request_impl(
            new_slug="enq-fail",
            thread=None,
            to="cursor",
            subject="DIRECTIVE",
            body="TYPE: DIRECTIVE",
            from_agent="web-anthropic",
            tags=None,
            sidecar_content=None,
            sidecar_slug=None,
            desired_model="auto",
            desired_effort="medium",
            contract="answer",
            require_attended=False,
            request_id="req-enq",
            after_turn=0,
            summary=None,
        )
    assert result["handler_status"] == "no-auto-handler"
    assert result["enqueue_failure"]["terminal_park"] is True
    assert result["enqueue_failure"]["reason"] == "enqueue_unreachable"
    assert result["enqueue_failure"]["error_class"] == "enqueue_unreachable"
    assert result["poll_hint"]["producer"] == "none"
    assert "enqueue_failure" in result
    record_mock.assert_called_once()
    assert record_mock.call_args.args[0] == "mcp.agentbus.request.degraded"


def test_request_transient_probe_then_arms():
    """T1 integration: eventual live probe reaches enqueue on armed path."""
    send_payload = {
        "send_path": "new_thread",
        "thread": {"id": "55", "slug": "retry-arm"},
        "turn": {"id": 1, "thread": "55", "turn_number": 1},
    }
    with (
        patch("tools.agent_bus.request._send_dispatch", return_value=send_payload),
        patch(
            "tools.agent_bus.request.probe_auto_liveness",
            return_value={
                "live": True,
                "reason": "ok",
                "attempts": 2,
                "elapsed_s": 1.0,
            },
        ),
        patch(
            "tools.agent_bus.request.enqueue_auto_job",
            return_value={
                "ok": True,
                "handler_status": "auto-admit-armed",
                "enqueue": {"ok": True},
            },
        ),
        patch("tools.agent_bus.request.record") as record_mock,
    ):
        result = _request_impl(
            new_slug="retry-arm",
            thread=None,
            to="cursor",
            subject="DIRECTIVE",
            body="TYPE: DIRECTIVE",
            from_agent="web-anthropic",
            tags=None,
            sidecar_content=None,
            sidecar_slug=None,
            desired_model="auto",
            desired_effort="medium",
            contract="answer",
            require_attended=False,
            request_id=None,
            after_turn=0,
            summary=None,
        )
    assert result["handler_status"] == "auto-admit-armed"
    assert "enqueue_failure" not in result
    assert "producer" not in result["poll_hint"]
    record_mock.assert_called_once_with(
        "mcp.agentbus.request.posted",
        thread="55",
        turn_number=1,
        handler_status="auto-admit-armed",
        desired_model="auto",
        contract="answer",
    )


def test_request_hoists_sidecar_uri_from_send():
    """Successful sidecar write must surface on request response (a:26439 #5)."""
    send_payload = {
        "send_path": "new_thread",
        "thread": {"id": "99", "slug": "sidecar-hoist"},
        "turn": {
            "id": 1,
            "thread": "99",
            "turn_number": 1,
            "sidecar_uri": "cortex://notes/system/threads/99-probe.md",
            "sidecar_sha256": "abc123",
        },
        "sidecar_uri": "cortex://notes/system/threads/99-probe.md",
        "sidecar_sha256": "abc123",
    }
    with (
        patch(
            "tools.agent_bus.request._send_dispatch",
            return_value=send_payload,
        ),
        patch(
            "tools.agent_bus.request.probe_auto_liveness",
            return_value={"live": False, "reason": "no_live_handler"},
        ),
    ):
        result = _request_impl(
            new_slug="sidecar-hoist",
            thread=None,
            to="cursor",
            subject="probe",
            body="brief",
            from_agent="web-anthropic",
            tags=None,
            sidecar_content="# probe",
            sidecar_slug="probe",
            desired_model="auto",
            desired_effort="medium",
            contract="answer",
            require_attended=False,
            request_id="req-test",
            after_turn=0,
            summary=None,
        )
    assert result["sidecar_uri"] == "cortex://notes/system/threads/99-probe.md"
    assert result["sidecar_sha256"] == "abc123"
    assert result["turn"]["sidecar_uri"] == result["sidecar_uri"]


def test_request_forwards_summary_on_mint():
    """Wire summary (so-what title) must reach send on new_slug path."""
    captured: dict[str, object] = {}

    def fake_send(**kwargs):
        captured.update(kwargs)
        return {
            "send_path": "new_thread",
            "thread": {"id": "42", "slug": "so-what", "summary": kwargs.get("summary")},
            "turn": {"id": 1, "thread": "42", "turn_number": 1},
        }

    with (
        patch("tools.agent_bus.request._send_dispatch", side_effect=fake_send),
        patch(
            "tools.agent_bus.request.probe_auto_liveness",
            return_value={"live": False, "reason": "no_live_handler"},
        ),
    ):
        result = _request_impl(
            new_slug="so-what",
            thread=None,
            to="cursor",
            subject="DIRECTIVE",
            body="TYPE: DIRECTIVE\nintent: x",
            from_agent="web-anthropic",
            tags=None,
            sidecar_content=None,
            sidecar_slug=None,
            desired_model="auto",
            desired_effort="medium",
            contract="answer",
            require_attended=False,
            request_id="req-test",
            after_turn=0,
            summary="ULG: auto-wake web consults on tick",
        )
    assert captured.get("summary") == "ULG: auto-wake web consults on tick"
    assert result["thread"]["summary"] == "ULG: auto-wake web consults on tick"


def test_request_resolves_so_what_from_body_when_summary_omitted():
    captured: dict[str, object] = {}

    def fake_send(**kwargs):
        captured.update(kwargs)
        return {
            "send_path": "new_thread",
            "thread": {"id": "43", "slug": "body-so-what"},
            "turn": {"id": 1, "thread": "43", "turn_number": 1},
        }

    with (
        patch("tools.agent_bus.request._send_dispatch", side_effect=fake_send),
        patch(
            "tools.agent_bus.request.probe_auto_liveness",
            return_value={"live": False, "reason": "no_live_handler"},
        ),
    ):
        _request_impl(
            new_slug="body-so-what",
            thread=None,
            to="cursor",
            subject="DIRECTIVE",
            body="TYPE: DIRECTIVE\nso_what: ULG gains reliable closeout SMS\nintent: x",
            from_agent="web-anthropic",
            tags=None,
            sidecar_content=None,
            sidecar_slug=None,
            desired_model="auto",
            desired_effort="medium",
            contract="answer",
            require_attended=False,
            request_id="req-test",
            after_turn=0,
            summary=None,
        )
    assert captured.get("summary") == "ULG gains reliable closeout SMS"
