"""Language firewall over life-facing response fields and registry strings."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from systems.frontier_consult.life_intent_routes import life_intent_router

from life_intent.proposal_store import clear_store, create_proposal
from life_intent.registry import load_registry
from life_intent.response_firewall import (
    FORBIDDEN_TOKENS,
    assert_life_facing_firewall,
    collect_registry_life_facing_strings,
    collect_response_field_strings,
    collect_work_order_strings,
    forbidden_hits,
    scan_texts,
)


def _route_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    mock_proxy = MagicMock()
    mock_proxy.event_bus = None
    monkeypatch.setattr(
        "systems.frontier_consult.life_intent_routes.get_proxy",
        lambda: mock_proxy,
        raising=False,
    )
    app = FastAPI()
    app.include_router(life_intent_router)
    return app


def test_registry_and_work_order_strings_pass_firewall() -> None:
    reg = load_registry()
    violations = scan_texts(
        [*collect_registry_life_facing_strings(reg), *collect_work_order_strings(reg)]
    )
    assert violations == {}


def test_forbidden_hits_detects_dispatch_tokens() -> None:
    assert forbidden_hits("use team_dispatch now") == ["dispatch", "team_dispatch"]
    assert forbidden_hits("plain scout language") == []


def test_response_firewall_over_propose_and_commit_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_store()
    client = TestClient(_route_app(monkeypatch))
    payloads: list[dict] = []

    propose_ok = client.post(
        "/api/v1/life/intent/propose",
        json={
            "intent": {
                "verb": "investigate",
                "subject": "reminder double-fire",
                "detail": "The reminder notification fires twice every morning around 8am.",
            }
        },
    ).json()
    payloads.append(propose_ok)

    propose_reject = client.post(
        "/api/v1/life/intent/propose",
        json={
            "intent": {
                "verb": "build",
                "subject": "feature",
                "detail": "Skip recon and go straight to implement now please.",
            }
        },
    ).json()
    payloads.append(propose_reject)

    monkeypatch.setenv("LIFE_INTENT_COMMIT_LIVE", "1")
    monkeypatch.setattr("life_intent.commit._write_packet", lambda _t, _s: "packet.md")
    monkeypatch.setattr("life_intent.commit._create_entity", lambda _s: None)
    monkeypatch.setattr(
        "life_intent.commit._fire_recon_dispatch",
        AsyncMock(return_value="agent-bus:life-intent-reminder-double-fire"),
    )

    proposal_id = propose_ok["proposal_id"]
    commit_ok = client.post(
        "/api/v1/life/intent/commit",
        json={"proposal_id": proposal_id},
    ).json()
    payloads.append(commit_ok)

    commit_reject = client.post(
        "/api/v1/life/intent/commit",
        json={"proposal_id": proposal_id},
    ).json()
    payloads.append(commit_reject)

    foreign_reject = client.post(
        "/api/v1/life/intent/commit",
        json={"proposal_id": "00000000-0000-4000-8000-000000000099"},
    ).json()
    payloads.append(foreign_reject)

    monkeypatch.delenv("LIFE_INTENT_COMMIT_LIVE", raising=False)
    gated = client.post(
        "/api/v1/life/intent/commit",
        json={"proposal_id": create_proposal(
            normalized_intent={
                "verb": "investigate",
                "subject": "latency",
                "detail": "Dashboard loads slowly on Monday mornings.",
            },
            work_order="scout",
            verb="investigate",
            lane="recon",
        )},
    ).json()
    payloads.append(gated)

    assert_life_facing_firewall(response_payloads=payloads)

    for payload in payloads:
        for text in collect_response_field_strings([payload]):
            for token in FORBIDDEN_TOKENS:
                assert token not in text.lower()
