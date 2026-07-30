"""Warn-tier briefing advisory unit tests."""

from __future__ import annotations

from unittest.mock import patch

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.body_auto_spill import prepare_body_for_insert
from agent_bus_store.body_briefing_advisory import (
    CHECKPOINT_PROFILE_SCHEMA_ADVISORY_MIN,
    CHECKPOINT_PROFILE_SILENT_MAX,
    briefing_advisory,
)
from agent_bus_store.turns_models import BRIEFING_TARGET_CHARS, MAX_TURN_BODY_CHARS
from fastapi.testclient import TestClient


def _app(tmp_path, monkeypatch):
    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    import cortex_store.dispatch_ops._thread_sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod, "_FILES_ROOT", cortex_root)
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app


def test_briefing_advisory_boundary_inclusive() -> None:
    body = "x" * BRIEFING_TARGET_CHARS
    assert (
        briefing_advisory(
            body=body, subject=None, allow_long_body=False, has_sidecar=False
        )
        is None
    )


def test_briefing_advisory_over_target() -> None:
    body = "x" * (BRIEFING_TARGET_CHARS + 1)
    advisory = briefing_advisory(
        body=body, subject=None, allow_long_body=False, has_sidecar=False
    )
    assert advisory is not None
    assert advisory.reason == "over_briefing_target"
    assert advisory.body_chars == len(body)


def test_briefing_advisory_directive_exempt() -> None:
    body = "TYPE: DIRECTIVE\n" + ("x" * BRIEFING_TARGET_CHARS)
    assert (
        briefing_advisory(
            body=body, subject=None, allow_long_body=False, has_sidecar=False
        )
        is None
    )


def test_briefing_advisory_allow_long_body_exempt() -> None:
    body = "x" * (BRIEFING_TARGET_CHARS + 1)
    assert (
        briefing_advisory(
            body=body, subject=None, allow_long_body=True, has_sidecar=False
        )
        is None
    )


def test_briefing_advisory_sidecar_exempt() -> None:
    body = "x" * (BRIEFING_TARGET_CHARS + 1)
    assert (
        briefing_advisory(
            body=body, subject=None, allow_long_body=False, has_sidecar=True
        )
        is None
    )


def test_briefing_advisory_checkpoint_exempt() -> None:
    body = "x" * (BRIEFING_TARGET_CHARS + 5000)
    assert (
        briefing_advisory(
            body=body,
            subject="CHECKPOINT — wave 3",
            allow_long_body=False,
            has_sidecar=False,
        )
        is None
    )


def test_birth_shaped_checkpoint_always_silent() -> None:
    body = "x" * (CHECKPOINT_PROFILE_SCHEMA_ADVISORY_MIN + 100)
    assert (
        briefing_advisory(
            body=body,
            subject="CHECKPOINT — birth",
            allow_long_body=False,
            has_sidecar=False,
            supersedes_turn=None,
        )
        is None
    )


def test_structural_checkpoint_silent_band_bootstrap() -> None:
    body = "x" * (BRIEFING_TARGET_CHARS + 3000)
    assert (
        briefing_advisory(
            body=body,
            subject="CHECKPOINT wave 2",
            allow_long_body=False,
            has_sidecar=False,
            thread_tags=[],
            supersedes_turn=1,
        )
        is None
    )
    assert body.__len__() <= CHECKPOINT_PROFILE_SILENT_MAX


def test_structural_checkpoint_silent_band_steady_state() -> None:
    body = "x" * (BRIEFING_TARGET_CHARS + 3000)
    assert (
        briefing_advisory(
            body=body,
            subject="CHECKPOINT wave 5",
            allow_long_body=False,
            has_sidecar=False,
            thread_tags=["role:root"],
            supersedes_turn=4,
        )
        is None
    )
    assert body.__len__() <= CHECKPOINT_PROFILE_SILENT_MAX


def test_structural_checkpoint_schema_shape_advisory() -> None:
    body = "x" * CHECKPOINT_PROFILE_SCHEMA_ADVISORY_MIN
    advisory = briefing_advisory(
        body=body,
        subject="CHECKPOINT wave 4",
        allow_long_body=False,
        has_sidecar=False,
        thread_tags=["role:root"],
        supersedes_turn=3,
    )
    assert advisory is not None
    assert advisory.reason == "checkpoint_schema_shape"
    assert advisory.suggestion != (
        "Write substantive content to a durable sidecar (sidecar_content on send or "
        "fs write to notes/system/threads/) and post a short briefing with a pointer."
    )
    assert advisory.turn_kind == "structural_checkpoint"


def test_default_profile_still_fires_over_target() -> None:
    body = "x" * (BRIEFING_TARGET_CHARS + 1)
    advisory = briefing_advisory(
        body=body,
        subject="Status update",
        allow_long_body=False,
        has_sidecar=False,
    )
    assert advisory is not None
    assert advisory.reason == "over_briefing_target"


def test_prepare_body_advisory_does_not_mutate_body() -> None:
    body = "y" * (BRIEFING_TARGET_CHARS + 1)
    prepared = prepare_body_for_insert(
        thread="1140",
        subject="warn",
        body=body,
        from_agent="cursor",
    )
    assert prepared.body == body
    assert prepared.advisory is not None


def test_prepare_body_auto_spill_suppresses_advisory(tmp_path, monkeypatch) -> None:
    body = "z" * (MAX_TURN_BODY_CHARS + 100)
    with patch(
        "agent_bus_store.body_auto_spill.write_thread_sidecar_for_send",
    ) as write_sidecar:
        write_sidecar.return_value = type(
            "Sidecar",
            (),
            {"uri": "cortex://notes/system/threads/1140-auto-overflow.md", "sha256": "abc"},
        )()
        prepared = prepare_body_for_insert(
            thread="1140",
            subject="spill",
            body=body,
            from_agent="cursor",
        )
    assert prepared.advisory is None
    assert prepared.sidecar_uri is not None


def test_post_turns_returns_advisory_and_emits_event(tmp_path, monkeypatch) -> None:
    body = "a" * (BRIEFING_TARGET_CHARS + 1)
    events: list[dict[str, object]] = []

    def _capture(signal: str, payload: dict[str, object], *, role: str = "observation") -> None:
        events.append({"signal": signal, "payload": payload, "role": role})

    monkeypatch.setattr(
        "agent_bus_store.events.turn_body_advisory._publish",
        _capture,
    )
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "advisory-seed",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "hello",
            },
        )
        assert seed.status_code == 201, seed.text
        thread_id = seed.json()["thread"]["id"]
        resp = client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "long inline",
                "body": body,
                "after_turn": 1,
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["briefing_advisory"]["reason"] == "over_briefing_target"
        turn = client.get(
            f"/turns/by-number?thread={thread_id}&turn_number=2"
        ).json()
        assert turn["body"] == body
        assert any(e["signal"] == "mcp.agentbus.turn.body_over_briefing" for e in events)
        advisory_events = [
            e for e in events if e["signal"] == "mcp.agentbus.turn.body_over_briefing"
        ]
        assert "body" not in advisory_events[0]["payload"]
