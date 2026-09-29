"""Request-surface ``hop`` verb — payload, validation, degrade, not a contract."""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock, patch

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.body_briefing_advisory import INLINE_CONTRACT_PREFIXES
from contract_vocab import CANONICAL_CONTRACTS
from fastapi.testclient import TestClient
from hop_handoff import (
    StandingHandoffFreshness,
    build_continuity_handoff_body,
    parse_successor_birth_id,
)

from tools.agent_bus.hop import _hop_dispatch
from tools.agent_bus.request_intake import reset_request_id_registry_for_tests
from tools.agent_bus.request_worker_client import enqueue_auto_job


def setup_function() -> None:
    reset_request_id_registry_for_tests()


_HANDOFF = StandingHandoffFreshness(
    status="current",
    uri="cortex://notes/system/threads/77-standing-handoff.md",
    mtime_epoch=1.0,
    age_s=10.0,
)


def test_hop_not_in_canonical_contracts() -> None:
    assert "hop" not in CANONICAL_CONTRACTS


def test_hop_dispatch_signature_rejects_new_slug() -> None:
    assert "new_slug" not in inspect.signature(_hop_dispatch).parameters


def test_hop_rejects_missing_thread() -> None:
    result = _hop_dispatch(reason="mcp-restart-healthy", from_agent="web-anthropic")
    assert result["reason"] == "hop_thread_required"


def test_hop_rejects_missing_reason() -> None:
    result = _hop_dispatch(thread="77", from_agent="web-anthropic")
    assert result["reason"] == "hop_reason_required"


def test_hop_impl_forwards_continuity_hop_and_handoff_body() -> None:
    captured: dict[str, object] = {}

    def fake_impl(**kwargs):
        captured.update(kwargs)
        return {
            "auto_handler_status": "auto-handler-live",
            "thread": {"id": "77"},
            "turn": {"turn_number": 2},
        }

    with (
        patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
        patch("tools.agent_bus.hop._request_impl", side_effect=fake_impl),
        patch("tools.agent_bus.hop.record"),
    ):
        result = _hop_dispatch(
            thread="77",
            reason="mcp-restart-healthy",
            from_agent="web-anthropic",
        )
    assert captured["continuity_hop"] is True
    assert captured["contract"] == "answer"
    assert captured["new_slug"] is None
    assert captured["thread"] == "77"
    body = str(captured["body"])
    first = next(line for line in body.splitlines() if line.strip())
    assert first == "TYPE: CONTINUITY_HANDOFF"
    assert "source: agent-bus-hop-verb" in body
    assert "trigger: mcp-restart-healthy" in body
    assert "successor_birth_id:" in body
    assert result["continuity_hop"] is True
    assert result["successor"]["handle"] == "successor_birth_id"
    assert result["successor"]["names"] == "successor"
    assert result["successor"]["value"]
    assert "predecessor's receipt" in result["successor"]["note"]
    assert result["auto_handler_status"] == "auto-handler-live"
    assert "status:done" not in str(result)


def test_hop_degrades_when_auto_dead() -> None:
    with (
        patch("tools.agent_bus.hop.assess_standing_handoff", return_value=_HANDOFF),
        patch(
            "tools.agent_bus.hop._request_impl",
            return_value={
                "auto_handler_status": "no-auto-handler",
                "enqueue_failure": {"reason": "no_live_handler", "terminal_park": True},
                "thread": {"id": "77"},
                "turn": {"turn_number": 1},
            },
        ),
        patch("tools.agent_bus.hop.record"),
    ):
        result = _hop_dispatch(
            thread=77,
            reason="mcp-restart-healthy",
            from_agent="web-anthropic",
        )
    assert result["auto_handler_status"] == "no-auto-handler"
    assert result["continuity_hop"] is True
    assert result["enqueue_failure"]["terminal_park"] is True


def test_enqueue_omits_continuity_hop_when_false() -> None:
    with patch("tools.agent_bus.request_worker_client.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b'{"ok": true}'
        resp.json.return_value = {"ok": True}
        client.post.return_value = resp
        enqueue_auto_job(
            thread_id="77",
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
    assert "continuity_hop" not in payload


def test_enqueue_includes_continuity_hop_when_true() -> None:
    with patch("tools.agent_bus.request_worker_client.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b'{"ok": true}'
        resp.json.return_value = {"ok": True}
        client.post.return_value = resp
        enqueue_auto_job(
            thread_id="77",
            turn_number=1,
            subject="s",
            body="TYPE: CONTINUITY_HANDOFF\n",
            from_agent="web-anthropic",
            to_agent="cursor",
            desired_model="auto",
            desired_effort="medium",
            contract="answer",
            continuity_hop=True,
        )
    payload = client.post.call_args.kwargs["json"]
    assert payload["continuity_hop"] is True


def _hop_store_app(tmp_path, monkeypatch):
    """Same temp-db harness as ``test_body_briefing_advisory._app``."""
    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    import cortex_store.dispatch_ops._thread_sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod, "_FILES_ROOT", cortex_root)
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app, cortex_root


def _relay_via_test_client(client: TestClient, sent: list[dict[str, Any]]):
    def relay(
        service: str,
        method: str,
        path: str,
        body: dict | None = None,
        **_kwargs: Any,
    ) -> dict:
        if service != "agent-bus":
            return {"error": f"unexpected relay: {service} {method} {path}"}
        if method == "GET":
            resp = client.get(path)
        elif method == "POST":
            if path == "/threads/send" and isinstance(body, dict):
                sent.append(body)
            resp = client.post(path, json=body)
        else:
            return {"error": f"unexpected relay: {service} {method} {path}"}
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            return {
                "error": f"HTTP {resp.status_code}",
                "status_code": resp.status_code,
                "detail": detail,
            }
        return resp.json()

    return relay


def test_hop_split_stores_header_under_briefing_shield(tmp_path, monkeypatch) -> None:
    """Lane 12286 specimen, observed 2026-09-29 00:0xZ.

    Unsplit hop body is 3045 characters and POST /turns refuses it. The hop
    verb stores the structural header and writes the doctrine tail to the sidecar.
    """
    handoff = StandingHandoffFreshness(
        status="current",
        uri="cortex://notes/system/threads/12286-standing-handoff.md",
        mtime_epoch=1.0,
        age_s=10.0,
    )
    specimen = build_continuity_handoff_body(
        thread_id="12286",
        trigger="r" * 38,
        source="agent-bus-hop-verb",
        handoff=handoff,
    )
    assert len(specimen) == 3045, len(specimen)

    app, cortex_root = _hop_store_app(tmp_path, monkeypatch)
    sent: list[dict[str, Any]] = []
    captured_enqueue: dict[str, Any] = {}

    def fake_enqueue(**kwargs: Any) -> dict[str, Any]:
        captured_enqueue.update(kwargs)
        return {"ok": True, "auto_handler_status": "auto-handler-live"}

    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "hop-split-seed",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "hello",
            },
        )
        assert seed.status_code == 201, seed.text
        thread_id = seed.json()["thread"]["id"]
        refused = client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "unsplit hop body",
                "body": specimen,
                "after_turn": 1,
            },
        )
        assert refused.status_code == 422, refused.text
        detail = refused.json()["detail"]
        assert detail["reason"] == "over_briefing_target"
        assert detail["body_chars"] == 3045
        assert detail["target_chars"] == 2000

        relay = _relay_via_test_client(client, sent)
        with (
            patch(
                "tools.agent_bus.hop.assess_standing_handoff",
                return_value=handoff,
            ),
            patch("tools.agent_bus.send.relay", side_effect=relay),
            patch("tools.agent_bus._shared.relay", side_effect=relay),
            patch(
                "tools.agent_bus.request.probe_auto_liveness",
                return_value={"live": True},
            ),
            patch(
                "tools.agent_bus.request.enqueue_auto_job",
                side_effect=fake_enqueue,
            ),
        ):
            result = _hop_dispatch(
                thread=thread_id,
                reason="r" * 38,
                from_agent="web-anthropic",
            )

        assert result.get("continuity_hop") is True, result
        stored = client.get(f"/turns/by-number?thread={thread_id}&turn_number=2").json()
        stored_body = stored["body"]
        assert len(stored_body) <= 2000
        assert stored_body.splitlines()[0] == "TYPE: CONTINUITY_HANDOFF"
        assert "successor_birth_id:" in stored_body
        assert "Sidecar:" in stored_body
        assert "KEEP-ALIVE" not in stored_body

        sidecar_uri = result["sidecar_uri"]
        sidecar_text = (cortex_root / sidecar_uri.removeprefix("cortex://")).read_text(
            encoding="utf-8"
        )
        assert "KEEP-ALIVE" in sidecar_text

        enqueue_body = str(captured_enqueue["body"])
        assert "KEEP-ALIVE" in enqueue_body
        assert parse_successor_birth_id(enqueue_body) == parse_successor_birth_id(
            stored_body
        )
        assert sent, "hop did not POST /threads/send"
        assert sent[-1].get("allow_long_body") is not True
        assert "TYPE: CONTINUITY_HANDOFF" not in INLINE_CONTRACT_PREFIXES
