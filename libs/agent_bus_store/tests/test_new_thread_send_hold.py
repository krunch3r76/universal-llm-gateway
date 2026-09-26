"""Part A: new-thread send holds projection outside the write transaction."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.body_auto_spill import PreparedBody
from agent_bus_store.checkpoint_projection import CheckpointBodyTooLargeError
from agent_bus_store.turns_models import MAX_TURN_BODY_CHARS
from cortex_store.dispatch_ops._thread_sidecar import SidecarWriteError
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline


@contextmanager
def _client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir(exist_ok=True)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    import cortex_store.dispatch_ops._thread_sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod, "_FILES_ROOT", cortex_root)
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    with TestClient(app) as client:
        yield client


def _send(**overrides: Any) -> dict[str, Any]:
    base = {
        "from": "cursor",
        "to": "web",
        "subject": "hold-test",
        "body": "brief body",
    }
    base.update(overrides)
    return base


def test_ac_a2_projection_outside_transaction_both_routes(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_bus_store.db import init_db
    from agent_bus_store.resume_fence_store import append_fence_event, mint_fence_id

    gate = threading.Event()
    release = threading.Event()
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    init_db()

    def _blocking_project(**kwargs: Any) -> str:
        gate.set()
        release.wait(timeout=5.0)
        return kwargs["body"]

    routes = (
        (
            "/threads/send",
            {"new_slug": "hold-send", **_send(subject="CHECKPOINT gate")},
        ),
        (
            "/threads/with-turn",
            {
                "slug": "hold-with-turn",
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT gate wt",
                "body": "x",
            },
        ),
    )

    for path, payload in routes:
        gate.clear()
        release.clear()
        route_tmp = tmp_path / path.strip("/").replace("/", "-")
        route_tmp.mkdir(exist_ok=True)
        with patch(
            "agent_bus_store.body_auto_spill.maybe_project_checkpoint_body",
            side_effect=_blocking_project,
        ):
            with _client(route_tmp, monkeypatch) as client:
                result: dict[str, Any] = {}
                error: list[BaseException] = []

                def _send_thread() -> None:
                    try:
                        resp = client.post(path, json=payload)
                        result["resp"] = resp
                    except BaseException as exc:
                        error.append(exc)

                sender = threading.Thread(target=_send_thread)
                sender.start()
                assert gate.wait(timeout=3.0), "projection block never entered"
                fid = mint_fence_id()
                t0 = time.monotonic()
                append_fence_event(
                    fence_id=fid,
                    root_thread="99999",
                    event="armed",
                    transcript_id="tab-hold",
                    payload={"source": "ac-a2"},
                )
                elapsed = time.monotonic() - t0
                assert elapsed < 1.0, f"append_fence_event blocked {elapsed:.2f}s"
                release.set()
                sender.join(timeout=10.0)
                assert not error, error
                resp = result["resp"]
                assert resp.status_code == 201, resp.text


def test_ac_a3_call_order_send_route(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    connect_depth = {"open": 0}

    real_connect = __import__(
        "agent_bus_store.db.connection", fromlist=["connect"]
    ).connect

    @contextmanager
    def _spy_connect():
        connect_depth["open"] += 1
        try:
            with real_connect() as conn:
                yield conn
        finally:
            connect_depth["open"] -= 1

    def _mint_side_effect(**kwargs: Any) -> tuple[dict[str, Any], float]:
        order.append("mint")
        from agent_bus_store.db.thread_mint import mint_thread

        return mint_thread(**kwargs)

    def _prepare_side_effect(**kwargs: Any) -> PreparedBody:
        order.append("prepare")
        assert connect_depth["open"] == 0, "connect open during prepare"
        return PreparedBody(body=kwargs["body"])

    def _lane_side_effect(**kwargs: Any) -> None:
        order.append("lane")

    def _insert_side_effect(**kwargs: Any) -> tuple[int, str, int]:
        order.append("insert")
        from agent_bus_store.db.turns import insert_turn

        return insert_turn(**kwargs)

    with (
        patch("agent_bus_store.routes.threads.new_thread_send.mint_thread", side_effect=_mint_side_effect),
        patch(
            "agent_bus_store.routes.threads.new_thread_send.prepare_body_for_insert",
            side_effect=_prepare_side_effect,
        ),
        patch(
            "agent_bus_store.routes.threads.new_thread_send._bind_lane_on_send",
            side_effect=_lane_side_effect,
        ),
        patch(
            "agent_bus_store.routes.threads.new_thread_send.insert_turn",
            side_effect=_insert_side_effect,
        ),
        patch("agent_bus_store.routes.threads.new_thread_send.emit_send_hold_measured"),
        patch("agent_bus_store.db.connection.connect", _spy_connect),
    ):
        with _client(tmp_path, monkeypatch) as client:
            parent = client.post(
                "/threads/send", json=_send(new_slug="order-parent")
            ).json()["thread"]["id"]
            resp = client.post(
                "/threads/send",
                json=_send(
                    new_slug="order-child",
                    parent_thread=parent,
                    lane_role="sub_mission",
                ),
            )
            assert resp.status_code == 201, resp.text
    assert order.index("mint") < order.index("lane") < order.index("prepare") < order.index("insert")


@pytest.mark.parametrize(
    ("path", "payload_factory"),
    [
        (
            "/threads/send",
            lambda slug: _send(new_slug=slug, subject="CHECKPOINT orphan"),
        ),
        (
            "/threads/with-turn",
            lambda slug: {
                "slug": slug,
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT orphan wt",
                "body": "y",
            },
        ),
    ],
)
def test_ac_a4_post_mint_checkpoint_too_large(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload_factory: Callable[[str], dict[str, Any]],
) -> None:
    slug = f"orphan-cp-{path.strip('/')}"
    with patch(
        "agent_bus_store.body_auto_spill.maybe_project_checkpoint_body",
        side_effect=lambda **kwargs: (_ for _ in ()).throw(
            CheckpointBodyTooLargeError(
                body_chars=9000,
                limit_chars=8000,
                authored_chars=6900,
                derived_chars=2100,
                cited_row_count=2,
                lane_row_count=1,
                compressed=True,
            )
        ),
    ):
        with patch(
            "agent_bus_store.routes.threads.send_prep.emit_thread_orphaned"
        ) as orphaned:
            with _client(tmp_path, monkeypatch) as client:
                resp = client.post(path, json=payload_factory(slug))
                assert resp.status_code == 413, resp.text
                detail = resp.json()["detail"]
                assert detail["created_thread"]
                assert detail["orphan_reason"] == "checkpoint_body_too_large"
                orphaned.assert_called_once()
                assert orphaned.call_args.kwargs["reason"] == "checkpoint_body_too_large"


def test_ac_a4_body_prepare_failed_http_exception(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    with patch(
        "agent_bus_store.routes.threads.new_thread_send.prepare_body_for_insert",
        side_effect=HTTPException(status_code=422, detail={"code": "checkpoint.speech_in_body"}),
    ):
        with patch(
            "agent_bus_store.routes.threads.send_prep.emit_thread_orphaned"
        ) as orphaned:
            with _client(tmp_path, monkeypatch) as client:
                resp = client.post(
                    "/threads/send",
                    json=_send(new_slug="speech-in-body", subject="CHECKPOINT"),
                )
                assert resp.status_code == 422, resp.text
                detail = resp.json()["detail"]
                assert detail["created_thread"]
                assert detail["orphan_reason"] == "body_prepare_failed"
                orphaned.assert_called_once()


def test_ac_a5_send_new_thread_response_shape(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expanded = "z" * (MAX_TURN_BODY_CHARS + 100)

    with patch(
        "agent_bus_store.body_auto_spill.maybe_project_checkpoint_body",
        side_effect=lambda **kwargs: expanded,
    ):
        with _client(tmp_path, monkeypatch) as client:
            resp = client.post(
                "/threads/send",
                json=_send(
                    new_slug="ac-a5-spill",
                    body="brief seed under pre-mint gate",
                    subject="status",
                ),
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()
            assert data["send_path"] == "new_thread"
            assert data["thread"]["id"]
            assert data["turn"]["turn_number"] == 1
            assert data["sidecar_uri"]
            assert data["sidecar_sha256"]


def test_ac_a6_hold_event_emitted(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, dict[str, Any], str]] = []

    def _capture(signal: str, payload: dict[str, Any], *, role: str) -> None:
        captured.append((signal, payload, role))

    with patch("agent_bus_store.events.send_hold._publish", side_effect=_capture):
        with _client(tmp_path, monkeypatch) as client:
            resp = client.post(
                "/threads/send",
                json=_send(new_slug="hold-event", subject="CHECKPOINT — evt"),
            )
            assert resp.status_code == 201, resp.text
    assert len(captured) == 1
    signal, payload, role = captured[0]
    assert signal == "mcp.agentbus.send.hold.measured"
    assert role == "observation"
    for key in (
        "thread",
        "route",
        "is_checkpoint",
        "body_chars",
        "spilled",
        "mint_hold_ms",
        "prepare_ms",
        "insert_call_ms",
    ):
        assert key in payload


def test_ac_a4_sidecar_write_failed_post_mint(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expanded = "x" * (MAX_TURN_BODY_CHARS + 50)

    with patch(
        "agent_bus_store.body_auto_spill.maybe_project_checkpoint_body",
        side_effect=lambda **kwargs: expanded,
    ):
        with patch(
            "agent_bus_store.body_auto_spill.write_thread_sidecar_for_send",
            side_effect=SidecarWriteError("disk"),
        ):
            with patch(
                "agent_bus_store.routes.threads.send_prep.emit_thread_orphaned"
            ) as orphaned:
                with _client(tmp_path, monkeypatch) as client:
                    resp = client.post(
                        "/threads/send",
                        json=_send(new_slug="sidecar-fail", body="small seed"),
                    )
                    assert resp.status_code == 503, resp.text
                    detail = resp.json()["detail"]
                    assert detail["created_thread"]
                    assert detail["orphan_reason"] == "sidecar_write_failed"
                    orphaned.assert_called_once()


def test_ac_a4_turn_insert_failed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    with patch(
        "agent_bus_store.routes.threads.new_thread_send.insert_turn",
        side_effect=RuntimeError("insert boom"),
    ):
        with patch(
            "agent_bus_store.routes.threads.send_prep.emit_thread_orphaned"
        ) as orphaned:
            with _client(tmp_path, monkeypatch) as client:
                resp = client.post(
                    "/threads/send",
                    json=_send(new_slug="insert-fail"),
                )
                assert resp.status_code == 500, resp.text
                detail = resp.json()["detail"]
                assert detail["code"] == "turn_insert_failed"
                assert detail["created_thread"]
                assert detail["orphan_reason"] == "turn_insert_failed"
                orphaned.assert_called_once()
