"""Seat registration and standing-bind gates on lane:cursor-auto threads."""

from __future__ import annotations

import pytest
from claude_bundles.operator_proxy_mission import MISSION_SKILL_SLUGS
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db
from agent_bus_store.db.connection import connect
from agent_bus_store.db.threads import set_thread_tags

pytestmark = pytest.mark.offline


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    app = create_app(db_path=str(db_path))
    app.dependency_overrides[require_token] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def _tag_lane_auto(thread_id: str) -> None:
    with connect() as conn:
        set_thread_tags(conn, thread_id, ["lane:cursor-auto"])


def _skills_line() -> str:
    return "skills_held: " + ", ".join(MISSION_SKILL_SLUGS)


def _admit_report_body(*, model: str, effort: str) -> str:
    return (
        "Auto admit-report (hop; no gate).\n"
        f"requested_model={model} resolved={model} (admit-plane)\n"
        "model_honored=True (admit-plane pin result)\n"
        f"requested_effort={effort} resolved={effort}\n"
    )


def _post_standing_bind(
    client: TestClient, thread_id: str, *, model: str, effort: str
) -> None:
    resp = client.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": "web-anthropic",
            "to": "cursor-auto",
            "subject": "standing bind",
            "body": f"TYPE: STANDING_BIND\nmodel: {model}\neffort: {effort}\n",
        },
    )
    assert resp.status_code == 201, resp.text


def _post_admit_report(
    client: TestClient, thread_id: str, *, model: str, effort: str
) -> None:
    resp = client.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": "cursor-auto",
            "to": "web-anthropic",
            "subject": "status:admit-report hop",
            "body": _admit_report_body(model=model, effort=effort),
        },
    )
    assert resp.status_code == 201, resp.text


def _registration_body(
    *,
    model: str,
    effort: str,
    open_children: str = "none",
    include_skills: bool = True,
) -> str:
    lines = ["TYPE: SEAT_REGISTRATION"]
    if include_skills:
        lines.append(_skills_line())
    lines.extend(
        [
            "- fetch-decision: runbook:maestro-loop in_context",
            "- fetch-decision: skill:retrieval-before-authoring in_context",
            f"open_children: {open_children}",
            f"model: {model}",
            f"effort: {effort}",
        ]
    )
    return "\n".join(lines) + "\n"


def test_missing_skills_held_422(bus_db: TestClient) -> None:
    parent = create_thread(thread_id=None, slug="sr-missing-skills")
    thread_id = parent["id"]
    _tag_lane_auto(thread_id)
    resp = bus_db.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": "web-anthropic",
            "to": "cursor-auto",
            "subject": "registration",
            "body": "TYPE: SEAT_REGISTRATION\nfetch-decision: x\n",
        },
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "MISSION_SKILL_SLUGS" in detail["fix_hint"]
    assert "lane-act-gates" in detail["fix_hint"]


def test_full_valid_registration_201(bus_db: TestClient) -> None:
    parent = create_thread(thread_id=None, slug="sr-valid")
    thread_id = parent["id"]
    _tag_lane_auto(thread_id)
    model = "cdp/opus-5.5-extra"
    effort = "high"
    _post_standing_bind(bus_db, thread_id, model=model, effort=effort)
    _post_admit_report(bus_db, thread_id, model=model, effort=effort)
    resp = bus_db.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": "web-anthropic",
            "to": "cursor-auto",
            "subject": "registration",
            "body": _registration_body(model=model, effort=effort),
        },
    )
    assert resp.status_code == 201, resp.text


def test_open_children_none_with_active_child_422(bus_db: TestClient) -> None:
    parent = create_thread(thread_id=None, slug="sr-open-parent")
    parent_id = parent["id"]
    child = create_thread(thread_id=None, slug="sr-open-child")
    child_id = child["id"]
    _tag_lane_auto(parent_id)
    bind = bus_db.post(
        f"/threads/{child_id}/lane-bind",
        json={"parent_thread_id": parent_id, "lane_role": "sub_mission"},
    )
    assert bind.status_code == 200, bind.text
    bus_db.post(
        "/threads/send",
        json={
            "thread": child_id,
            "from": "cursor-sdk",
            "to": "web-anthropic",
            "subject": "work",
            "body": "TYPE: CONFER\nactive child\n",
        },
    )
    model = "cdp/opus-5.5-extra"
    effort = "high"
    _post_standing_bind(bus_db, parent_id, model=model, effort=effort)
    _post_admit_report(bus_db, parent_id, model=model, effort=effort)
    resp = bus_db.post(
        "/threads/send",
        json={
            "thread": parent_id,
            "from": "web-anthropic",
            "to": "cursor-auto",
            "subject": "registration",
            "body": _registration_body(model=model, effort=effort, open_children="none"),
        },
    )
    assert resp.status_code == 422
    assert child_id in resp.json()["detail"]["fix_hint"]


def test_cursor_auto_stamp_exempt_201(bus_db: TestClient) -> None:
    parent = create_thread(thread_id=None, slug="sr-cursor-auto")
    thread_id = parent["id"]
    _tag_lane_auto(thread_id)
    resp = bus_db.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": "cursor-auto",
            "to": "web-anthropic",
            "subject": f"TYPE: SEAT_REGISTRATION — thread {thread_id}",
            "body": "TYPE: SEAT_REGISTRATION\nprojection only\n",
        },
    )
    assert resp.status_code == 201, resp.text


def test_seat_registration_refuses_unparsed_fetch_decision(bus_db: TestClient) -> None:
    parent = create_thread(thread_id=None, slug="sr-unparsed-fd")
    thread_id = parent["id"]
    _tag_lane_auto(thread_id)
    model = "cdp/opus-5.5-extra"
    effort = "high"
    _post_standing_bind(bus_db, thread_id, model=model, effort=effort)
    _post_admit_report(bus_db, thread_id, model=model, effort=effort)
    body = _registration_body(model=model, effort=effort).replace(
        "- fetch-decision: runbook:maestro-loop in_context\n", "fetch-decision: runbook:maestro-loop\n"
    )
    resp = bus_db.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": "web-anthropic",
            "to": "cursor-auto",
            "subject": "registration",
            "body": body,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "seat_registration_fetch_decision"


def test_seat_registration_refuses_skipped_maestro_receipt(bus_db: TestClient) -> None:
    parent = create_thread(thread_id=None, slug="sr-skipped-runbook")
    thread_id = parent["id"]
    _tag_lane_auto(thread_id)
    model = "cdp/opus-5.5-extra"
    effort = "high"
    _post_standing_bind(bus_db, thread_id, model=model, effort=effort)
    _post_admit_report(bus_db, thread_id, model=model, effort=effort)
    body = _registration_body(model=model, effort=effort).replace(
        "- fetch-decision: runbook:maestro-loop in_context\n",
        "- fetch-decision: runbook:maestro-loop skipped reason=not_in_context\n",
    )
    resp = bus_db.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": "web-anthropic",
            "to": "cursor-auto",
            "subject": "registration",
            "body": body,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "seat_registration_fetch_decision"


def test_retrieval_skill_ref_has_a_gate(bus_db: TestClient) -> None:
    parent = create_thread(thread_id=None, slug="sr-skill-gate")
    thread_id = parent["id"]
    _tag_lane_auto(thread_id)
    model = "cdp/opus-5.5-extra"
    effort = "high"
    _post_standing_bind(bus_db, thread_id, model=model, effort=effort)
    _post_admit_report(bus_db, thread_id, model=model, effort=effort)
    body = _registration_body(model=model, effort=effort).replace(
        "- fetch-decision: skill:retrieval-before-authoring in_context\n", ""
    )
    resp = bus_db.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": "web-anthropic",
            "to": "cursor-auto",
            "subject": "registration",
            "body": body,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "seat_registration_retrieval_skill"

    body_skipped = _registration_body(model=model, effort=effort).replace(
        "- fetch-decision: skill:retrieval-before-authoring in_context\n",
        "- fetch-decision: skill:retrieval-before-authoring skipped reason=not_in_context\n",
    )
    resp2 = bus_db.post(
        "/threads/send",
        json={
            "thread": thread_id,
            "from": "web-anthropic",
            "to": "cursor-auto",
            "subject": "registration",
            "body": body_skipped,
        },
    )
    assert resp2.status_code == 422
    assert resp2.json()["detail"]["reason"] == "seat_registration_retrieval_skill"


def test_standing_bind_replaces_model_tag_preserves_lane(bus_db: TestClient) -> None:
    parent = create_thread(thread_id=None, slug="sr-standing-tags")
    thread_id = parent["id"]
    _tag_lane_auto(thread_id)
    _post_standing_bind(bus_db, thread_id, model="cdp/opus-5.5", effort="xhigh")
    _post_standing_bind(bus_db, thread_id, model="cdp/opus-5.5-extra", effort="high")
    resp = bus_db.get(f"/threads/{thread_id}")
    assert resp.status_code == 200
    tags = resp.json().get("tags") or []
    assert "lane:cursor-auto" in tags
    assert "standing-model:cdp/opus-5.5-extra" in tags
    assert "standing-effort:high" in tags
    assert "standing-model:cdp/opus-5.5" not in tags
    assert "standing-effort:xhigh" not in tags
