"""Regression: send new_slug must not commit a thread before hoisted gates."""

from __future__ import annotations

from unittest.mock import patch

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.turns_models import MAX_TURN_BODY_CHARS
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


def _send_payload(**overrides):
    base = {
        "from": "cursor",
        "to": "web",
        "subject": "mint-order",
        "body": "brief",
    }
    base.update(overrides)
    return base


def _thread_count_for_slug(client: TestClient, slug: str) -> int:
    threads = client.get("/threads").json()["threads"]
    return sum(1 for t in threads if t["slug"] == slug)


def test_ac1_invalid_lane_role_sidecar_does_not_mint_thread(
    tmp_path, monkeypatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    slug = "mint-lane-422"
    with TestClient(app) as client:
        parent = client.post(
            "/threads/send",
            json=_send_payload(new_slug="mint-parent"),
        ).json()["thread"]["id"]
        resp = client.post(
            "/threads/send",
            json=_send_payload(
                new_slug=slug,
                sidecar_content="# payload",
                parent_thread=parent,
                lane_role="worker",
            ),
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "invalid_lane_role"
        assert _thread_count_for_slug(client, slug) == 0


def test_ac1_body_too_large_sidecar_does_not_mint_thread(
    tmp_path, monkeypatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    slug = "mint-body-413"
    oversized = "x" * (MAX_TURN_BODY_CHARS + 1)
    with TestClient(app) as client:
        resp = client.post(
            "/threads/send",
            json=_send_payload(
                new_slug=slug,
                sidecar_content="# small",
                body=oversized,
            ),
        )
        assert resp.status_code == 413, resp.text
        assert resp.json()["detail"]["reason"] == "body_too_large"
        assert _thread_count_for_slug(client, slug) == 0


def test_ac1_invalid_lane_role_plain_new_slug_does_not_mint_thread(tmp_path) -> None:
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    slug = "mint-plain-lane"
    with TestClient(app) as client:
        parent = client.post(
            "/threads/send",
            json=_send_payload(new_slug="mint-plain-parent"),
        ).json()["thread"]["id"]
        resp = client.post(
            "/threads/send",
            json=_send_payload(
                new_slug=slug,
                parent_thread=parent,
                lane_role="worker",
            ),
        )
        assert resp.status_code == 422, resp.text
        assert _thread_count_for_slug(client, slug) == 0


def test_ac3_post_mint_sidecar_write_failure_names_created_thread(
    tmp_path, monkeypatch
) -> None:
    from cortex_store.dispatch_ops._thread_sidecar import SidecarWriteError

    app = _app(tmp_path, monkeypatch)
    with (
        TestClient(app) as client,
        patch(
            "cortex_store.dispatch_ops._thread_sidecar.write_thread_sidecar_for_send",
            side_effect=SidecarWriteError("disk full"),
        ),
        patch(
            "agent_bus_store.routes.threads.send_prep.emit_thread_orphaned"
        ) as orphaned,
    ):
        resp = client.post(
            "/threads/send",
            json=_send_payload(
                new_slug="post-mint-sidecar",
                sidecar_content="payload",
            ),
        )
        assert resp.status_code == 503, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "sidecar_write_failed"
        assert detail["created_thread"]
        orphaned.assert_called_once()
        assert orphaned.call_args.kwargs["thread_id"] == detail["created_thread"]


def test_ac4_continue_path_binds_lane_when_parent_and_role_set(tmp_path) -> None:
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    with TestClient(app) as client:
        parent = client.post(
            "/threads/send",
            json=_send_payload(new_slug="continue-bind-parent"),
        ).json()["thread"]["id"]
        child = client.post(
            "/threads/send",
            json=_send_payload(new_slug="continue-bind-child"),
        ).json()["thread"]["id"]
        resp = client.post(
            "/threads/send",
            json=_send_payload(
                thread=child,
                parent_thread=parent,
                lane_role="sub_mission",
                subject="bind on continue",
            ),
        )
        assert resp.status_code == 201, resp.text
        current = client.get(f"/threads/{child}/lane-current").json()
        assert current["state"] == "associated"
        assert current["parent_thread"] == parent
        assert current["lane_role"] == "sub_mission"
