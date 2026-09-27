"""Post-enqueue registry write failure is an advisory on a success receipt."""

from __future__ import annotations

from unittest.mock import patch

from tools.agent_bus.request import _request_impl

_CSE_URL = "https://claude.ai/cowork/cse_sideeffect"


def test_transitions_writer_raises_receipt_ok_with_advisory() -> None:
    send_payload = {
        "send_path": "continue",
        "thread": {"id": "12286", "slug": "operator-ear-house"},
        "turn": {"id": 246, "thread": "12286", "turn_number": 246},
    }
    enqueued = {
        "ok": True,
        "auto_handler_status": "auto-handler-live",
        "job_admission": {"outcome": "admitted"},
    }

    def _erofs(**_kwargs: object) -> None:
        raise OSError(
            30,
            "Read-only file system",
            "/home/mcp/.gateway/cdp-registry/session_transitions.jsonl",
        )

    with (
        patch("tools.agent_bus.request._send_dispatch", return_value=send_payload),
        patch(
            "tools.agent_bus.request.probe_auto_liveness",
            return_value={"live": True, "attempts": 1, "elapsed_s": 0.0},
        ),
        patch(
            "tools.agent_bus.request.enqueue_auto_job", return_value=enqueued
        ) as enqueue,
        patch("tools.agent_bus.request_cse_bind.relay", return_value={"ok": True}),
        patch("tools.agent_bus.request.record"),
        patch(
            "claude_bundles.cse_session_obligations.stamp_session_ids",
            side_effect=_erofs,
        ),
        patch(
            "tools.agent_bus.cse_provenance_enrich.enrich_request_provenance",
            return_value={"ok": False, "reason": "no_episode"},
        ),
    ):
        result = _request_impl(
            new_slug=None,
            thread="12286",
            to="cursor",
            subject="DIRECTIVE",
            body="TYPE: DIRECTIVE",
            from_agent="web-anthropic",
            tags=None,
            sidecar_content=None,
            sidecar_slug=None,
            desired_model="auto",
            desired_effort="auto",
            contract="implement",
            require_attended=False,
            request_id="req-side-effect",
            after_turn=0,
            summary=None,
            cse_chat_url=_CSE_URL,
            cse_registration_id="reg-a",
        )

    assert "error" not in result
    assert result["turn"]["id"] == 246
    assert result["enqueue"]["ok"] is True
    enqueue.assert_called_once()
    failures = result["side_effect_failures"]
    assert failures[0]["op"] == "stamp_session_ids"
    assert "Read-only file system" in failures[0]["error"]
    assert "session_transitions.jsonl" in failures[0]["error"]
