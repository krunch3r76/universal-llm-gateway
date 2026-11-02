"""K115 paste-lane separation gate (offline)."""

from __future__ import annotations

import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import init_db, paste_lane_for
from agent_bus_store.db.connection import connect
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline


def _app(tmp_path, monkeypatch: pytest.MonkeyPatch):
    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    import cortex_store.dispatch_ops._thread_sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod, "_FILES_ROOT", cortex_root)
    init_db()
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app, cortex_root


def _mint_house(client: TestClient, slug: str = "k115-house") -> str:
    resp = client.post(
        "/threads/with-turn",
        json={
            "slug": slug,
            "from": "cursor",
            "to": "web",
            "subject": "HOUSE-BIRTH|init",
            "body": "house init",
            "tags": ["role:root"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["thread"]["id"]


def _mint_paste_child(
    client: TestClient, house_id: str, *, slug: str = "k115-paste"
) -> str:
    resp = client.post(
        "/threads/send",
        json={
            "new_slug": slug,
            "from": "cursor",
            "to": "web",
            "subject": "paste lane",
            "body": "paste child",
            "parent_thread": house_id,
            "lane_role": "side",
            "tags": ["lane:paste"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["thread"]["id"]


def _turn_count(client: TestClient, thread_id: str) -> int:
    return len(client.get(f"/turns?thread={thread_id}").json()["turns"])


def test_send_continue_refuses_chat_turn_on_role_root_house(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        house_id = _mint_house(client)
        paste_id = _mint_paste_child(client, house_id)
        before = _turn_count(client, house_id)
        resp = client.post(
            "/threads/send",
            json={
                "thread": house_id,
                "from": "cursor",
                "to": "web",
                "subject": "STEER|worker",
                "body": "probe status",
            },
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "house_chat_turn_requires_paste_lane"
        assert detail["house_thread_id"] == house_id
        assert detail["paste_lane_thread_id"] == paste_id
        assert detail["subject_head"] == "STEER"
        assert _turn_count(client, house_id) == before
        ok = client.post(
            "/threads/send",
            json={
                "thread": paste_id,
                "from": "cursor",
                "to": "web",
                "subject": "STEER|worker",
                "body": "probe status",
            },
        )
        assert ok.status_code == 201, ok.text


def test_send_sidecar_continue_refuses_before_sidecar_file_write(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, cortex_root = _app(tmp_path, monkeypatch)
    sidecar_dir = cortex_root / "notes/system/threads"
    with TestClient(app) as client:
        house_id = _mint_house(client, slug="k115-sidecar-house")
        paste_id = _mint_paste_child(client, house_id, slug="k115-sidecar-paste")
        before = _turn_count(client, house_id)
        resp = client.post(
            "/threads/send",
            json={
                "thread": house_id,
                "from": "cursor",
                "to": "web",
                "subject": "PROBE|check",
                "body": "see sidecar",
                "sidecar_content": "# Probe output\nDetails",
                "sidecar_slug": "probe",
            },
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "house_chat_turn_requires_paste_lane"
        assert detail["paste_lane_thread_id"] == paste_id
        assert _turn_count(client, house_id) == before
        if sidecar_dir.is_dir():
            assert list(sidecar_dir.glob(f"{house_id}-*.md")) == []


def test_post_turns_reply_route_refuses_chat_turn_on_role_root_house(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        house_id = _mint_house(client, slug="k115-reply-house")
        paste_id = _mint_paste_child(client, house_id, slug="k115-reply-paste")
        before = _turn_count(client, house_id)
        resp = client.post(
            "/turns",
            json={
                "thread": house_id,
                "from": "cursor",
                "to": "web",
                "subject": "READBACK|verify",
                "body": "readback body",
            },
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "house_chat_turn_requires_paste_lane"
        assert detail["paste_lane_thread_id"] == paste_id
        assert _turn_count(client, house_id) == before


def test_allowlist_catches_untagged_prose_and_milestone_heads_while_permitting_charter_turns(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        house_id = _mint_house(client, slug="k115-allow-house")
        _mint_paste_child(client, house_id, slug="k115-allow-paste")

        chat_subjects = [
            "status check on worker",
            "Re: hop 2",
            "",
            "DONE|slice 1",
            "QUESTION|open item",
        ]
        for subj in chat_subjects:
            resp = client.post(
                "/threads/send",
                json={
                    "thread": house_id,
                    "from": "cursor",
                    "to": "web",
                    "subject": subj,
                    "body": "chat body",
                },
            )
            assert resp.status_code == 422, f"subject={subj!r}: {resp.text}"
            assert (
                resp.json()["detail"]["code"] == "house_chat_turn_requires_paste_lane"
            )

        charter_subjects = [
            "COMMISSION|todo:x",
            "DECOMMISSION|todo:x",
            "WAKE|todo:x",
            "ACK|todo:x",
            "RULING|todo:x",
            "AMEND|todo:x",
            "PARK|todo:x",
            "CANCEL|todo:x",
            "CHECKPOINT|hop 1",
            "HOUSE-BIRTH|init",
            "CUTOVER|hop 2",
            "RELEASE|done",
            "DECISION|bind",
            "OBJECTIVE|goal",
            "CLOSEOUT|done",
            "DIRECTIVE|run",
            "DISPOSITION|closed",
        ]
        for subj in charter_subjects:
            resp = client.post(
                "/threads/send",
                json={
                    "thread": house_id,
                    "from": "cursor",
                    "to": "web",
                    "subject": subj,
                    "body": "charter body",
                },
            )
            assert resp.status_code == 201, f"subject={subj!r}: {resp.text}"

        type_bodies = [
            "TYPE: ROUTE/CONTROL\nkind: WAKE\n",
            "TYPE: DIRECTIVE\nrun it\n",
            "TYPE: DISPOSITION\nclosed\n",
            "TYPE: CLOSEOUT\ndone\n",
        ]
        for body in type_bodies:
            resp = client.post(
                "/threads/send",
                json={
                    "thread": house_id,
                    "from": "cursor",
                    "to": "web",
                    "subject": "protocol",
                    "body": body,
                },
            )
            assert resp.status_code == 201, f"body={body!r}: {resp.text}"


def test_single_consecutive_pointer_rule_on_house_thread(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        house_id = _mint_house(client, slug="k115-pointer-house")

        assert (
            client.post(
                "/threads/send",
                json={
                    "thread": house_id,
                    "from": "cursor",
                    "to": "web",
                    "subject": "DECISION|start",
                    "body": "go",
                },
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/threads/send",
                json={
                    "thread": house_id,
                    "from": "cursor",
                    "to": "web",
                    "subject": "POINTER|21721#1",
                    "body": "ptr1",
                },
            ).status_code
            == 201
        )
        second = client.post(
            "/threads/send",
            json={
                "thread": house_id,
                "from": "cursor",
                "to": "web",
                "subject": "POINTER|21721#2",
                "body": "ptr2",
            },
        )
        assert second.status_code == 422, second.text
        assert second.json()["detail"]["code"] == "house_chat_turn_requires_paste_lane"

        assert (
            client.post(
                "/threads/send",
                json={
                    "thread": house_id,
                    "from": "cursor",
                    "to": "web",
                    "subject": "RULING|next",
                    "body": "rule",
                },
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/threads/send",
                json={
                    "thread": house_id,
                    "from": "cursor",
                    "to": "web",
                    "subject": "POINTER|21721#3",
                    "body": "ptr3",
                },
            ).status_code
            == 201
        )


def test_missing_paste_child_returns_422_with_mint_instructions(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        house_id = _mint_house(client, slug="k115-no-paste-house")

        for subj in ("CHECKPOINT|init", "DECISION|charter"):
            assert (
                client.post(
                    "/threads/send",
                    json={
                        "thread": house_id,
                        "from": "cursor",
                        "to": "web",
                        "subject": subj,
                        "body": "ok",
                    },
                ).status_code
                == 201
            )

        resp = client.post(
            "/threads/send",
            json={
                "thread": house_id,
                "from": "cursor",
                "to": "web",
                "subject": "STEER|worker",
                "body": "probe",
            },
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "house_chat_turn_requires_paste_lane"
        assert detail["house_thread_id"] == house_id
        assert detail["paste_lane_thread_id"] is None
        fix = detail["fix_hint"]
        assert "new_slug" in fix
        assert house_id in fix
        assert "lane_role='side'" in fix
        assert "['lane:paste']" in fix


def test_two_paste_children_resolves_newest_active_by_max_association_id(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        house_id = _mint_house(client, slug="k115-two-paste-house")
        child_1 = _mint_paste_child(client, house_id, slug="k115-paste-a")
        child_2 = _mint_paste_child(client, house_id, slug="k115-paste-b")
        assert paste_lane_for(house_id) == child_2

        resp = client.post(
            "/threads/send",
            json={
                "thread": house_id,
                "from": "cursor",
                "to": "web",
                "subject": "LOG|status",
                "body": "log",
            },
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["paste_lane_thread_id"] == child_2

        close = client.patch(f"/threads/{child_2}/close", json={"summary": "done"})
        assert close.status_code == 200, close.text
        assert paste_lane_for(house_id) == child_1

        resp2 = client.post(
            "/threads/send",
            json={
                "thread": house_id,
                "from": "cursor",
                "to": "web",
                "subject": "LOG|status",
                "body": "log",
            },
        )
        assert resp2.status_code == 422
        assert resp2.json()["detail"]["paste_lane_thread_id"] == child_1


def test_lane_liaison_rejected_on_child_write_but_specimen_21721_shape_resolves_on_read(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        house_id = _mint_house(client, slug="k115-liaison-house")

        bad = client.post(
            "/threads/send",
            json={
                "new_slug": "bad-paste",
                "from": "cursor",
                "to": "web",
                "subject": "mint",
                "body": "bad",
                "parent_thread": house_id,
                "lane_role": "side",
                "tags": ["lane:liaison", "lane:paste"],
            },
        )
        assert bad.status_code == 422, bad.text
        assert bad.json()["detail"]["error"] == "liaison_tag_on_child_lane"

        child_id = _mint_paste_child(client, house_id, slug="k115-good-paste")
        with connect() as conn:
            conn.execute(
                "INSERT INTO thread_tags (thread_id, tag) VALUES (?, ?)",
                (child_id, "lane:liaison"),
            )
            conn.commit()
        assert paste_lane_for(house_id) == child_id
