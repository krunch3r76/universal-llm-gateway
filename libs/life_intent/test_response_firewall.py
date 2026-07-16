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


def test_forbidden_hits_rejects_colon_and_equals_dispatch_vocab() -> None:
    """Sol F1 — registry refuse_list colon forms must hit the scanner."""
    probes = {
        "role: reviewer": ["role:"],
        "contract: light-bounded": ["contract:"],
        "model: cursor/gpt-5.6-sol": ["model:"],
        "op: generate": ["op:"],
        "role=reviewer": ["role="],
        "model=cursor/gpt-5.6-sol": ["model="],
    }
    for text, expected_subset in probes.items():
        hits = forbidden_hits(text)
        for token in expected_subset:
            assert token in hits, f"{text!r} missing {token!r}; got {hits}"


def test_document_card_next_hints_pass_firewall() -> None:
    """Fable sibling 5 — document `_next` must stay refuse-list clean."""
    from cortex_store.card_adapters.document import ocr_companion_next_hint

    with_companion = ocr_companion_next_hint(
        {
            "ocr_uri": "cortex://notes/docs/example.ocr.md",
            "source_uri": "cortex://notes/docs/example.pdf",
        }
    )
    absent = ocr_companion_next_hint(
        {"source_uri": "cortex://notes/docs/example.pdf"}
    )
    assert with_companion is not None
    assert absent is not None
    assert forbidden_hits(with_companion) == []
    assert forbidden_hits(absent) == []
    assert "extract_document" not in absent.lower()


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
    monkeypatch.setattr("life_intent.commit._ensure_entity", lambda _s: None)
    monkeypatch.setattr(
        "life_intent.commit._prepare_recon_handle",
        AsyncMock(
            return_value=type(
                "H",
                (),
                {
                    "request_id": "r",
                    "execution_id": "e",
                    "dispatch_id": "r-aa",
                    "thread_id": "agent-bus:life-intent-reminder-double-fire",
                },
            )()
        ),
    )
    monkeypatch.setattr(
        "life_intent.commit._submit_prepared_handle",
        AsyncMock(return_value="agent-bus:life-intent-reminder-double-fire"),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_prepare.handle_to_dict",
        lambda h: {
            "request_id": h.request_id,
            "execution_id": h.execution_id,
            "dispatch_id": h.dispatch_id,
            "thread_id": h.thread_id,
            "resolved_model": "m",
            "role": "cursor-sdk",
            "family": "cursor",
            "platform": "sdk",
            "to_agent": "t",
            "handoff_contract": "light-bounded",
            "packet_path": "packet.md",
            "message": None,
            "caller_agent": "web-anthropic",
            "read_only": False,
            "aligned_knobs": None,
            "prompt_preamble": None,
            "thread_subject": "s",
            "pointer_body": "p",
            "effective_bus_lifecycle": "persistent",
            "parent_dispatch_thread_id": None,
            "dispatch_thread_id": None,
            "density_triage": None,
            "review_opt_out_reason_code": None,
            "auto_review_child": False,
            "auto_review_defaulted": False,
            "claimed_via_atomic": False,
            "admitted": True,
            "alignment_warnings": [],
            "knob_resolution": [],
        },
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_prepare.handle_from_dict",
        lambda data: type("H", (), data)(),
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
        json={
            "proposal_id": create_proposal(
                normalized_intent={
                    "verb": "investigate",
                    "subject": "latency",
                    "detail": "Dashboard loads slowly on Monday mornings.",
                },
                work_order="scout",
                verb="investigate",
                lane="recon",
            )
        },
    ).json()
    payloads.append(gated)

    assert_life_facing_firewall(response_payloads=payloads)

    assert "recon_ref" in commit_ok
    assert "dispatch_ref" not in commit_ok
    assert commit_ok["committed"] is True

    for payload in payloads:
        for text in collect_response_field_strings([payload]):
            for token in FORBIDDEN_TOKENS:
                assert token not in text.lower()


def test_firewall_rejects_dispatch_ref_key() -> None:
    with pytest.raises(AssertionError, match="dispatch"):
        assert_life_facing_firewall(
            response_payloads=[{"committed": True, "dispatch_ref": "agent-bus:x"}]
        )
