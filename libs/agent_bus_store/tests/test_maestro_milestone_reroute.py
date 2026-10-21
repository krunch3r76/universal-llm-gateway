"""L17/L21 compose-site maestro milestone allowlist and house reroute."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.routes.threads.send_prep import (
    MAESTRO_MILESTONE_PREFIXES,
    MAESTRO_REROUTE_TO_HOUSE_PREFIXES,
    apply_maestro_milestone_reroute,
    maestro_subject_head,
)
from bus_watch.endpoint_wake_dispatcher import qualifies
from fastapi.testclient import TestClient


def test_maestro_subject_head_token() -> None:
    assert maestro_subject_head("PLAN r2|scope") == "PLAN"
    assert maestro_subject_head("AMEND #712|scope") == "AMEND"
    assert maestro_subject_head("FACT|pickup|x") == "FACT"
    assert maestro_subject_head("TYPE: DIRECTIVE — go") == "TYPE:"


@pytest.mark.parametrize(
    "prefix",
    sorted(MAESTRO_MILESTONE_PREFIXES),
)
def test_milestone_prefixes_stay_on_maestro(prefix: str) -> None:
    house = "grok-bot-bl1"
    subject = f"{prefix}|work-key"
    assert (
        apply_maestro_milestone_reroute(
            to="maestro", from_agent=house, subject=subject
        )
        == "maestro"
    )
    assert (
        apply_maestro_milestone_reroute(
            to="web-anthropic", from_agent=house, subject=subject
        )
        == "web-anthropic"
    )


@pytest.mark.parametrize("prefix", sorted(MAESTRO_REROUTE_TO_HOUSE_PREFIXES))
def test_pickup_fact_reroute_to_house(prefix: str) -> None:
    house = "grok-bot-bl1"
    subject = f"{prefix}|detail"
    assert (
        apply_maestro_milestone_reroute(
            to="maestro", from_agent=house, subject=subject
        )
        == house
    )
    assert (
        apply_maestro_milestone_reroute(
            to="web-anthropic", from_agent=house, subject=subject
        )
        == house
    )
    assert not qualifies(
        {
            "to_agent": "maestro",
            "from_agent": house,
            "subject": subject,
        },
        cse=True,
    )


def test_verdict_question_qualify_after_compose_policy() -> None:
    for subject in ("VERDICT|pass", "QUESTION|blocker"):
        assert qualifies(
            {
                "to_agent": "maestro",
                "from_agent": "grok-bot-bl1",
                "subject": subject,
            },
            cse=True,
        )


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app


def test_http_send_reroutes_pickup_fact_to_maestro(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    house = "grok-bot-bl1"
    with TestClient(app) as client:
        thread = client.post(
            "/threads/send",
            json={
                "new_slug": "l17-reroute",
                "from": house,
                "to": "maestro",
                "subject": "seed",
                "body": "open lane",
            },
        ).json()["thread"]["id"]
        for n, subject in enumerate(("PICKUP|work-1", "FACT|house-cp|16934|1"), start=2):
            client.post(
                "/threads/send",
                json={
                    "thread": thread,
                    "from": house,
                    "to": "maestro",
                    "subject": subject,
                    "body": "telemetry",
                },
            )
            stored = client.get(
                f"/turns/by-number?thread={thread}&turn_number={n}"
            ).json()
            assert stored["to"] == house
            assert stored["subject"] == subject
        client.post(
            "/threads/send",
            json={
                "thread": thread,
                "from": house,
                "to": "maestro",
                "subject": "VERDICT|ok",
                "body": "milestone",
            },
        )
        verdict = client.get(
            f"/turns/by-number?thread={thread}&turn_number=4"
        ).json()
        assert verdict["to"] == "maestro"


@pytest.mark.parametrize(
    "subject",
    ("PICKUP|new-slug-work", "FACT|house-cp|16934|1"),
)
def test_http_new_slug_reroutes_pickup_fact_to_house(
    tmp_path, monkeypatch, subject: str
) -> None:
    app = _app(tmp_path, monkeypatch)
    house = "cursor"
    with TestClient(app) as client:
        created = client.post(
            "/threads/send",
            json={
                "new_slug": f"l17-new-{subject.split('|', 1)[0].lower()}",
                "from": house,
                "to": "web-anthropic",
                "subject": subject,
                "body": "birth turn reroute",
            },
        )
        assert created.status_code == 201, created.text
        thread = created.json()["thread"]["id"]
        stored = client.get(
            f"/turns/by-number?thread={thread}&turn_number=1"
        ).json()
        assert stored["to"] == house
        assert stored["subject"] == subject


@pytest.mark.parametrize(
    "subject",
    ("PICKUP|sidecar-work", "FACT|pickup|12797|hop-B"),
)
def test_http_sidecar_content_reroutes_pickup_fact_to_house(
    tmp_path, monkeypatch, subject: str
) -> None:
    app = _app(tmp_path, monkeypatch)
    house = "grok-bot-bl1"
    with TestClient(app) as client:
        parent = client.post(
            "/threads/send",
            json={
                "new_slug": "l17-sidecar-parent",
                "from": house,
                "to": house,
                "subject": "seed",
                "body": "parent",
            },
        ).json()["thread"]["id"]
        created = client.post(
            "/threads/send",
            json={
                "thread": parent,
                "from": house,
                "to": "maestro",
                "subject": subject,
                "body": "inline",
                "sidecar_content": "sidecar payload for reroute test",
            },
        )
        assert created.status_code == 201, created.text
        turn_n = int(created.json()["turn"]["turn_number"])
        stored = client.get(
            f"/turns/by-number?thread={parent}&turn_number={turn_n}"
        ).json()
        assert stored["to"] == house
        assert stored["subject"] == subject


MCP_ROOT = Path(__file__).resolve().parents[3] / "services" / "mcp-server"
sys.path.insert(0, str(MCP_ROOT))
from tools import agent_bus as agent_bus_module  # noqa: E402


@pytest.mark.parametrize(
    "subject,to_addr",
    (
        ("PICKUP|mcp-work", "maestro"),
        ("FACT|house-cp|16934|2", "web-anthropic"),
    ),
)
def test_mcp_send_dispatch_reroutes_pickup_fact(subject: str, to_addr: str) -> None:
    house = "cursor"
    relay_calls: list[dict] = []

    def _relay(
        service: str, method: str, path: str, **kwargs: object
    ) -> dict[str, object]:
        assert service == "agent-bus"
        relay_calls.append(kwargs.get("body") or {})  # type: ignore[arg-type]
        return {
            "send_path": "existing_thread",
            "thread": {"id": "9001"},
            "turn": {"turn_number": 2},
        }

    with patch.object(agent_bus_module, "_relay", side_effect=_relay):
        with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
            result = agent_bus_module._send_dispatch(
                thread="9001",
                from_agent=house,
                to=to_addr,
                subject=subject,
                body="mcp reroute",
            )
    assert "error" not in result
    assert relay_calls[0]["to"] == house
    assert relay_calls[0]["subject"] == subject
