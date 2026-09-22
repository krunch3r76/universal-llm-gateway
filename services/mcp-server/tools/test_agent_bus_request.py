"""Arm-predicate + lane-tag tests for agent_bus.request (MCP package).

The worker transport (``httpx``) lives in ``request_worker_client`` after the
SLOC split, so liveness patches target that module; ``request`` re-exports the
names it calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tools.agent_bus.request import (
    _merge_lane_tags,
    _request_dispatch,
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
    """F1: unreachable / non-live ⇒ live=False (never auto-handler-live)."""
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
                "auto_handler_status": "no-auto-handler",
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
    assert result["auto_handler_status"] == "no-auto-handler"
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
                "auto_handler_status": "auto-handler-live",
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
    assert result["auto_handler_status"] == "auto-handler-live"
    assert "enqueue_failure" not in result
    assert "producer" not in result["poll_hint"]
    record_mock.assert_called_once_with(
        "mcp.agentbus.request.posted",
        thread="55",
        turn_number=1,
        auto_handler_status="auto-handler-live",
        job_admission_outcome="not_applicable",
        desired_model="auto",
        contract="answer",
    )


def test_request_posted_emit_carries_ledger_request_id():
    """Posted signal must carry the resolved ledger request_id for cursor-auto trace."""
    send_payload = {
        "send_path": "new_thread",
        "thread": {"id": "55", "slug": "req-index"},
        "turn": {"id": 1, "thread": "55", "turn_number": 1},
    }
    with (
        patch("tools.agent_bus.request._send_dispatch", return_value=send_payload),
        patch(
            "tools.agent_bus.request.probe_auto_liveness",
            return_value={
                "live": True,
                "reason": "ok",
                "attempts": 1,
                "elapsed_s": 0.0,
            },
        ),
        patch(
            "tools.agent_bus.request.enqueue_auto_job",
            return_value={
                "ok": True,
                "auto_handler_status": "auto-handler-live",
                "enqueue": {"ok": True},
            },
        ),
        patch("tools.agent_bus.request.record") as record_mock,
    ):
        result = _request_impl(
            new_slug="req-index",
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
            request_id="ledger-req-abc123",
            after_turn=0,
            summary=None,
        )
    assert result["request_id"] == "ledger-req-abc123"
    record_mock.assert_called_once_with(
        "mcp.agentbus.request.posted",
        thread="55",
        turn_number=1,
        auto_handler_status="auto-handler-live",
        job_admission_outcome="not_applicable",
        desired_model="auto",
        contract="answer",
        request_id="ledger-req-abc123",
    )


def test_request_promotes_same_thread_lane_counts():
    """Lane discriminant is reachable without digging past enqueue.enqueue."""
    send_payload = {
        "send_path": "new_thread",
        "thread": {"id": "6885", "slug": "lane-count"},
        "turn": {"id": 1, "thread": "6885", "turn_number": 2},
    }
    with (
        patch("tools.agent_bus.request._send_dispatch", return_value=send_payload),
        patch(
            "tools.agent_bus.request.probe_auto_liveness",
            return_value={
                "live": True,
                "reason": "ok",
                "attempts": 1,
                "elapsed_s": 0.0,
            },
        ),
        patch(
            "tools.agent_bus.request.enqueue_auto_job",
            return_value={
                "ok": True,
                "auto_handler_status": "auto-handler-live",
                "enqueue": {
                    "ok": True,
                    "superseded": None,
                    "same_thread_pending": 1,
                    "same_thread_claimed": 0,
                },
            },
        ),
        patch("tools.agent_bus.request.record"),
    ):
        result = _request_impl(
            new_slug="lane-count",
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
            contract="implement",
            require_attended=False,
            request_id=None,
            after_turn=0,
            summary=None,
        )
    assert result["same_thread_pending"] == 1
    assert result["same_thread_claimed"] == 0
    assert result["enqueue"]["same_thread_pending"] == 1
    assert result["enqueue"]["enqueue"]["same_thread_pending"] == 1
    assert result["enqueue"]["enqueue"]["superseded"] is None


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


def test_request_new_slug_enrolls_lifecycle_active():
    """A′ coverage: mission lane birth must pass lifecycle_state=active."""
    captured: dict[str, object] = {}

    def fake_send(**kwargs):
        captured.update(kwargs)
        return {
            "send_path": "new_thread",
            "thread": {
                "id": "6901",
                "slug": "mission-enroll",
                "bus_lifecycle_state": "active",
            },
            "turn": {"id": 1, "thread": "6901", "turn_number": 1},
        }

    with (
        patch("tools.agent_bus.request._send_dispatch", side_effect=fake_send),
        patch(
            "tools.agent_bus.request.probe_auto_liveness",
            return_value={"live": False, "reason": "no_live_handler"},
        ),
    ):
        _request_impl(
            new_slug="mission-enroll",
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
            contract="implement",
            require_attended=False,
            request_id=None,
            after_turn=0,
            summary=None,
        )
    assert captured.get("lifecycle_state") == "active"


def test_request_continue_does_not_pass_lifecycle_state():
    """Continue path must not send lifecycle_state (send rejects it)."""
    captured: dict[str, object] = {}

    def fake_send(**kwargs):
        captured.update(kwargs)
        return {
            "send_path": "continue",
            "thread": {"id": "6885", "slug": "existing"},
            "turn": {"id": 2, "thread": "6885", "turn_number": 2},
        }

    with (
        patch("tools.agent_bus.request._send_dispatch", side_effect=fake_send),
        patch(
            "tools.agent_bus.request.probe_auto_liveness",
            return_value={"live": False, "reason": "no_live_handler"},
        ),
        patch(
            "tools.agent_bus._shared.relay",
            return_value={"id": "6885", "tags": []},
        ),
    ):
        _request_impl(
            new_slug=None,
            thread="6885",
            to="cursor",
            subject="follow-up",
            body="continue",
            from_agent="web-anthropic",
            tags=None,
            sidecar_content=None,
            sidecar_slug=None,
            desired_model="auto",
            desired_effort="medium",
            contract="answer",
            require_attended=False,
            request_id=None,
            after_turn=1,
            summary=None,
        )
    assert captured.get("lifecycle_state") is None


def test_request_continue_loads_role_root_tags_via_relay():
    """Continue/hop must read tags from the host socket, not messages.db."""
    captured: dict[str, object] = {}
    paths: list[str] = []

    def fake_send(**kwargs):
        captured.update(kwargs)
        return {
            "send_path": "continue",
            "thread": {"id": "12286", "slug": "operator-ear-house"},
            "turn": {"id": 75, "thread": "12286", "turn_number": 75},
        }

    def fake_relay(service, method, path, **kwargs):
        assert service == "agent-bus"
        assert method == "GET"
        paths.append(path)
        return {"id": "12286", "tags": ["role:root", "type:continuity"]}

    with (
        patch("tools.agent_bus.request._send_dispatch", side_effect=fake_send),
        patch(
            "tools.agent_bus.request.probe_auto_liveness",
            return_value={"live": False, "reason": "no_live_handler"},
        ),
        patch("tools.agent_bus._shared.relay", side_effect=fake_relay),
        patch("agent_bus_store.db.threads.get_thread") as get_thread,
    ):
        _request_impl(
            new_slug=None,
            thread="12286",
            to="cursor",
            subject="follow-up",
            body="so_what: must not replace the house summary",
            from_agent="dispatch",
            tags=None,
            sidecar_content=None,
            sidecar_slug=None,
            desired_model="auto",
            desired_effort="medium",
            contract="answer",
            require_attended=False,
            request_id=None,
            after_turn=74,
            summary=None,
        )
    assert paths == ["/threads/12286/summary?recent=1"]
    assert captured.get("summary") is None
    get_thread.assert_not_called()


def test_enqueue_omits_lane_when_unset() -> None:
    """AC-4: MCP→GIW enqueue must not send lane when the caller omitted it."""
    from tools.agent_bus.request_worker_client import enqueue_auto_job

    with patch("tools.agent_bus.request_worker_client.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b'{"ok": true}'
        resp.json.return_value = {"ok": True}
        client.post.return_value = resp
        enqueue_auto_job(
            thread_id="7224",
            turn_number=1,
            subject="s",
            body="b",
            from_agent="web-anthropic",
            to_agent="cursor",
            desired_model="auto",
            desired_effort="medium",
            contract="answer",
        )
    payload = client.post.call_args.kwargs["json"]
    assert "lane" not in payload


def test_request_omit_desired_effort_enqueues_auto() -> None:
    send_payload = {
        "send_path": "new_thread",
        "thread": {"id": "9923", "slug": "effort-omit"},
        "turn": {"id": 1, "thread": "9923", "turn_number": 1},
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
            },
        ),
        patch("tools.agent_bus.request.enqueue_auto_job") as enqueue_mock,
    ):
        enqueue_mock.return_value = {
            "ok": True,
            "auto_handler_status": "auto-handler-live",
            "enqueue": {"ok": True},
        }
        _request_dispatch(
            new_slug="effort-omit",
            thread=None,
            to="cursor",
            subject="DIRECTIVE",
            body="TYPE: DIRECTIVE",
            from_agent="web-anthropic",
            contract="investigate",
        )
    assert enqueue_mock.call_args.kwargs["desired_effort"] == "auto"


def test_enqueue_includes_lane_when_set() -> None:
    from tools.agent_bus.request_worker_client import enqueue_auto_job

    with patch("tools.agent_bus.request_worker_client.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b'{"ok": true}'
        resp.json.return_value = {"ok": True}
        client.post.return_value = resp
        enqueue_auto_job(
            thread_id="7224",
            turn_number=1,
            subject="s",
            body="b",
            from_agent="web-anthropic",
            to_agent="cursor",
            desired_model="auto",
            desired_effort="medium",
            contract="answer",
            lane="A",
        )
    payload = client.post.call_args.kwargs["json"]
    assert payload["lane"] == "A"


def test_enqueue_omits_workspace_when_unset() -> None:
    from tools.agent_bus.request_worker_client import enqueue_auto_job

    with patch("tools.agent_bus.request_worker_client.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b'{"ok": true}'
        resp.json.return_value = {"ok": True}
        client.post.return_value = resp
        enqueue_auto_job(
            thread_id="7224",
            turn_number=1,
            subject="s",
            body="b",
            from_agent="web-anthropic",
            to_agent="cursor",
            desired_model="auto",
            desired_effort="medium",
            contract="ask",
        )
    payload = client.post.call_args.kwargs["json"]
    assert "workspace" not in payload


def test_enqueue_includes_workspace_when_set() -> None:
    from tools.agent_bus.request_worker_client import enqueue_auto_job

    with patch("tools.agent_bus.request_worker_client.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b'{"ok": true}'
        resp.json.return_value = {"ok": True}
        client.post.return_value = resp
        enqueue_auto_job(
            thread_id="7224",
            turn_number=1,
            subject="s",
            body="b",
            from_agent="web-anthropic",
            to_agent="cursor",
            desired_model="auto",
            desired_effort="medium",
            contract="ask",
            workspace="claudeburst",
        )
    payload = client.post.call_args.kwargs["json"]
    assert payload["workspace"] == "claudeburst"


def test_enqueue_includes_prompt_uri_when_set() -> None:
    from tools.agent_bus.request_worker_client import enqueue_auto_job

    with patch("tools.agent_bus.request_worker_client.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b'{"ok": true}'
        resp.json.return_value = {"ok": True}
        client.post.return_value = resp
        enqueue_auto_job(
            thread_id="9530",
            turn_number=1,
            subject="s",
            body="b",
            from_agent="web-anthropic",
            to_agent="cursor",
            desired_model="auto",
            desired_effort="medium",
            contract="confer",
            prompt_uri="cortex://notes/system/threads/9530-brief.md",
            advisor_brief="sealed",
        )
    payload = client.post.call_args.kwargs["json"]
    assert payload["prompt_uri"] == "cortex://notes/system/threads/9530-brief.md"
    assert payload["advisor_brief"] == "sealed"


def test_request_dispatch_accepts_prompt_uri() -> None:
    import inspect

    from tools.agent_bus.request import _request_dispatch

    params = inspect.signature(_request_dispatch).parameters
    assert "prompt_uri" in params
    assert "advisor_brief" in params


_CSE_URL = "https://claude.ai/cowork/cse_01CodB7tom1281iY8BmZJcZM"


def test_request_binds_cse_when_liveness_dead():
    """Turn write + Cowork URL stamps thread CSE even when Auto is down."""
    send_payload = {
        "send_path": "new_thread",
        "thread": {"id": "9501", "slug": "cse-bind"},
        "turn": {"id": 1, "thread": "9501", "turn_number": 1},
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
        patch("tools.agent_bus.request_cse_bind.relay") as bind,
        patch("claude_bundles.cse_session_obligations.stamp_session_ids") as stamp,
        patch("tools.agent_bus.request.record"),
    ):
        result = _request_impl(
            new_slug="cse-bind",
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
            request_id="req-cse",
            after_turn=0,
            summary=None,
            cse_chat_url=_CSE_URL,
            cse_registration_id="reg-a",
        )
    assert result["thread"]["id"] == "9501"
    bind.assert_called_once()
    assert bind.call_args.args[0] == "agent-bus"
    assert bind.call_args.args[1] == "POST"
    assert bind.call_args.args[2] == "/threads/9501/cse-associate"
    assert bind.call_args.kwargs["body"]["cse_chat_url"] == _CSE_URL
    assert bind.call_args.kwargs["body"]["cse_registration_id"] == "reg-a"
    stamp.assert_not_called()


def test_request_registration_only_does_not_bind_cse():
    send_payload = {
        "send_path": "new_thread",
        "thread": {"id": "9501", "slug": "cse-reg-only"},
        "turn": {"id": 1, "thread": "9501", "turn_number": 1},
    }
    with (
        patch("tools.agent_bus.request._send_dispatch", return_value=send_payload),
        patch(
            "tools.agent_bus.request.probe_auto_liveness",
            return_value={"live": False, "reason": "no_live_handler", "attempts": 1},
        ),
        patch("tools.agent_bus.request_cse_bind.relay") as bind,
        patch("tools.agent_bus.request.record"),
    ):
        _request_impl(
            new_slug="cse-reg-only",
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
            request_id="req-reg",
            after_turn=0,
            summary=None,
            cse_chat_url=None,
            cse_registration_id="reg-only",
        )
    bind.assert_not_called()


def test_request_half_pair_parent_only_rejects_before_send() -> None:
    """Half-pair lane bind must 422 at MCP intake with no thread row created."""
    with patch("tools.agent_bus.request._send_dispatch") as send_mock:
        result = _request_dispatch(
            new_slug="half-pair-parent",
            thread=None,
            to="cursor",
            subject="DIRECTIVE",
            body="TYPE: DIRECTIVE",
            from_agent="web-anthropic",
            parent_thread="10479",
            lane_role=None,
        )
    assert result["reason"] == "lane_bind_incomplete"
    assert result["provided"] == ["parent_thread"]
    send_mock.assert_not_called()


def test_request_half_pair_role_only_rejects_before_send() -> None:
    with patch("tools.agent_bus.request._send_dispatch") as send_mock:
        result = _request_dispatch(
            new_slug="half-pair-role",
            thread=None,
            to="cursor",
            subject="DIRECTIVE",
            body="TYPE: DIRECTIVE",
            from_agent="web-anthropic",
            parent_thread=None,
            lane_role="sub_mission",
        )
    assert result["reason"] == "lane_bind_incomplete"
    assert result["provided"] == ["lane_role"]
    send_mock.assert_not_called()


def test_request_cursor_author_does_not_bind_cse():
    send_payload = {
        "send_path": "new_thread",
        "thread": {"id": "77", "slug": "cursor-author"},
        "turn": {"id": 1, "thread": "77", "turn_number": 1},
    }
    with (
        patch("tools.agent_bus.request._send_dispatch", return_value=send_payload),
        patch(
            "tools.agent_bus.request.probe_auto_liveness",
            return_value={"live": False, "reason": "no_live_handler", "attempts": 1},
        ),
        patch("tools.agent_bus.request_cse_bind.relay") as bind,
        patch("tools.agent_bus.request.record"),
    ):
        _request_impl(
            new_slug="cursor-author",
            thread=None,
            to="cursor",
            subject="DIRECTIVE",
            body="TYPE: DIRECTIVE",
            from_agent="cursor",
            tags=None,
            sidecar_content=None,
            sidecar_slug=None,
            desired_model="auto",
            desired_effort="medium",
            contract="answer",
            require_attended=False,
            request_id="req-cursor",
            after_turn=0,
            summary=None,
            cse_chat_url=_CSE_URL,
            cse_registration_id="reg-a",
        )
    bind.assert_not_called()


def test_continuity_hop_from_cursor_binds_registration():
    """IDE hop must record the wire registration. Census reads that row."""
    send_payload = {
        "send_path": "continue",
        "thread": {"id": "12286", "slug": "operator-ear-house"},
        "turn": {"id": 144, "thread": "12286", "turn_number": 144},
    }
    with (
        patch("tools.agent_bus.request._send_dispatch", return_value=send_payload),
        patch(
            "tools.agent_bus.request.probe_auto_liveness",
            return_value={"live": True, "attempts": 1, "elapsed_s": 0.0},
        ),
        patch("tools.agent_bus.request_cse_bind.relay") as bind,
        patch(
            "tools.agent_bus.request.enqueue_auto_job",
            return_value={
                "ok": True,
                "auto_handler_status": "auto-handler-live",
                "job_admission": {"outcome": "not_applicable"},
            },
        ) as enqueue,
        patch("claude_bundles.cse_session_obligations.stamp_session_ids") as stamp,
        patch("tools.agent_bus.request.record"),
    ):
        _request_impl(
            new_slug=None,
            thread="12286",
            to="cursor",
            subject="CONTINUITY HANDOFF",
            body="TYPE: CONTINUITY_HANDOFF",
            from_agent="cursor",
            tags=None,
            sidecar_content=None,
            sidecar_slug=None,
            desired_model="auto",
            desired_effort="auto",
            contract="answer",
            require_attended=False,
            request_id="req-hop",
            after_turn=138,
            summary=None,
            cse_chat_url=_CSE_URL,
            cse_registration_id="reg-a",
            continuity_hop=True,
        )
    bind.assert_called_once()
    assert bind.call_args.kwargs["body"]["cse_registration_id"] == "reg-a"
    assert enqueue.call_args.kwargs["cse_registration_id"] == "reg-a"
    stamp.assert_not_called()
