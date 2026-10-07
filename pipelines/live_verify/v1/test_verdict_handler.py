"""Verdict step records one assertion; close refuses before the sidecar."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from cortex_store.workflow_state import VERDICT_MISSING

from pipelines.live_verify.v1.handlers._ops import (
    relations_from_snapshot,
    ruling_attributes,
    verdict_claim,
)
from pipelines.live_verify.v1.handlers.verdict import LiveVerifyVerdictHandler
from pipelines.todo_close.v1.handlers import close as close_mod

pytestmark = pytest.mark.offline


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class _Client:
    def __init__(self, routes: dict[str, dict]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    async def post(self, path: str, json: dict) -> _Response:  # noqa: A002
        tool = json["tool"]
        self.calls.append(tool)
        return _Response(self.routes[tool])

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _patch_client(monkeypatch: pytest.MonkeyPatch, module, client: _Client) -> None:
    monkeypatch.setattr(
        module,
        "make_async_client",
        lambda *_a, **_k: client,
    )


def test_unreachable_probe_stores_unknown_answer() -> None:
    relations = relations_from_snapshot(
        {"error": "manage socket not found"},
        ["cortex_api"],
    )
    assert relations == [
        {
            "service": "cortex_api",
            "relation": None,
            "answer": "unknown",
            "relation_absent_reason": "manage socket not found",
            "close_gate": "live_verify.land_sha_not_live",
        }
    ]


def test_answer_yes_equal_is_stored() -> None:
    snapshot = {
        "services": [
            {
                "service": "cortex_api",
                "code_ref_validation": {
                    "liveness": {"answer": "yes", "relation": "ancestor"}
                },
            }
        ]
    }
    relations = relations_from_snapshot(snapshot, ["cortex_api"])
    assert relations[0]["answer"] == "yes"
    assert relations[0]["relation"] == "ancestor"
    assert "relation_absent_reason" not in relations[0]


def test_null_stargate_relation_records_probe_reason_without_inventing() -> None:
    snapshot = {
        "services": [
            {
                "service": "stargate",
                "code_ref_validation": {
                    "liveness": {
                        "answer": "unknown",
                        "relation": None,
                        "reason": "probe unreachable or returned no payload",
                        "observation": {"probe_error": "HTTP timeout"},
                    }
                },
            }
        ]
    }
    relations = relations_from_snapshot(snapshot, ["stargate"])
    assert relations[0]["relation"] is None
    assert relations[0]["answer"] == "unknown"
    assert relations[0]["relation_absent_reason"] == (
        "probe unreachable or returned no payload"
    )
    assert relations[0]["close_gate"] == "live_verify.land_sha_not_live"


@pytest.mark.asyncio
async def test_verdict_supersedes_prior_and_drops_ruling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(
        {
            "entity_get": {
                "id": "todo:sample",
                "description": "(a) row exists",
                "assertions": [
                    {
                        "id": 7,
                        "superseded_by": None,
                        "attributes": {
                            "kind": "live_verify",
                            "operator_ruling": "old ruling",
                            "lines": [],
                        },
                    }
                ],
            },
            "supersede": {"id": 8},
        }
    )
    _patch_client(
        monkeypatch,
        __import__("pipelines.live_verify.v1.handlers.verdict", fromlist=["verdict"]),
        client,
    )
    monkeypatch.setattr(
        "pipelines.live_verify.v1.handlers.verdict.fleet_liveness",
        lambda **_k: {"error": "down"},
    )
    monkeypatch.setattr(
        "pipelines.live_verify.v1.handlers.verdict.make_async_client",
        lambda *_a, **_k: client,
    )
    out = await LiveVerifyVerdictHandler().execute(
        None,
        SimpleNamespace(
            options={
                "todo_id": "todo:sample",
                "land_sha": "abc",
                "services": ["cortex_api"],
                "lines": [{"line": "(a) row exists", "verdict": "LIVE_OK"}],
            }
        ),
    )
    body = out.json
    assert body["ok"] is True
    assert "assert" not in client.calls
    assert client.calls.count("supersede") == 1
    assert "operator_ruling" not in body or body.get("operator_ruling") is None
    claim = verdict_claim(body["lines"])
    assert "LIVE_OK" in claim
    assert "(a) row exists" in claim


@pytest.mark.asyncio
async def test_ruling_is_supersede_of_full_attribute_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class _Capture(_Client):
        async def post(self, path: str, json: dict) -> _Response:  # noqa: A002
            self.calls.append(json["tool"])
            if json["tool"] == "supersede":
                captured.update(json["arguments"])
            return _Response(self.routes[json["tool"]])

    client = _Capture(
        {
            "entity_get": {
                "assertions": [
                    {
                        "id": 3,
                        "superseded_by": None,
                        "attributes": {
                            "kind": "live_verify",
                            "land_sha": "abc",
                            "services": ["cortex_api"],
                            "lines": [
                                {
                                    "line": "(f) ruling",
                                    "probe_call": {"method": "fleet_liveness"},
                                    "observed": {},
                                    "verdict": "LIVE_UNTESTABLE",
                                }
                            ],
                            "service_relations": [
                                {
                                    "service": "cortex_api",
                                    "relation": "equal",
                                    "answer": "yes",
                                }
                            ],
                        },
                    }
                ]
            },
            "supersede": {"id": 4},
        }
    )
    monkeypatch.setattr(
        "pipelines.live_verify.v1.handlers.verdict.make_async_client",
        lambda *_a, **_k: client,
    )
    out = await LiveVerifyVerdictHandler().execute(
        None,
        SimpleNamespace(
            options={
                "todo_id": "todo:sample",
                "land_sha": "abc",
                "services": ["cortex_api"],
                "operator_ruling": "accepted",
            }
        ),
    )
    assert out.json["ok"] is True
    assert captured["old_assertion_id"] == 3
    assert captured["derivation_type"] == "agent_observation"
    assert captured["attributes"]["operator_ruling"] == "accepted"
    assert captured["attributes"]["lines"][0]["verdict"] == "LIVE_UNTESTABLE"
    assert "assertion_update" not in client.calls
    assert (
        ruling_attributes(captured["attributes"], "accepted")["operator_ruling"]
        == "accepted"
    )


@pytest.mark.asyncio
async def test_close_refusal_skips_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client(
        {
            "entity_get": {
                "attributes": {"live_verify_required": True},
                "assertions": [],
            }
        }
    )

    def _sidecar(*_a, **_k):
        raise AssertionError("sidecar must not run")

    monkeypatch.setattr(close_mod, "make_async_client", lambda *_a, **_k: client)
    monkeypatch.setattr(close_mod, "do_sidecar", _sidecar)
    out = await close_mod.TodoCloseApplyHandler().execute(
        None,
        SimpleNamespace(options={"todo_id": "todo:flagged", "summary": "close it"}),
    )
    assert out.json["error"] == VERDICT_MISSING
    assert client.calls == ["entity_get"]


@pytest.mark.asyncio
async def test_http_422_keeps_cortex_quality_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Reject(_Client):
        async def post(self, path: str, json: dict) -> _Response:  # noqa: A002
            self.calls.append(json["tool"])
            if json["tool"] == "assert":
                return _Response(
                    {
                        "detail": {
                            "error": "assertion_quality_rejected",
                            "diagnostics": [
                                {
                                    "field": "derivation_type",
                                    "message": "derivation_type is required",
                                }
                            ],
                        }
                    },
                    status_code=422,
                )
            return _Response(self.routes[json["tool"]])

    client = _Reject(
        {"entity_get": {"id": "todo:sample", "description": "", "assertions": []}}
    )
    monkeypatch.setattr(
        "pipelines.live_verify.v1.handlers.verdict.make_async_client",
        lambda *_a, **_k: client,
    )
    monkeypatch.setattr(
        "pipelines.live_verify.v1.handlers.verdict.fleet_liveness",
        lambda **_k: {"error": "down"},
    )
    out = await LiveVerifyVerdictHandler().execute(
        None,
        SimpleNamespace(
            options={
                "todo_id": "todo:sample",
                "land_sha": "abc",
                "services": ["cortex_api"],
                "lines": [{"line": "(a) row exists", "verdict": "LIVE_OK"}],
            }
        ),
    )
    error = out.json["error"]
    assert "http_422" in error
    assert "assertion_quality_rejected" in error
    assert "derivation_type is required" in error


def test_assert_payload_passes_live_quality_contract() -> None:
    from cortex_store.assertion_quality import validate_assertion
    from cortex_store.models.assertions import AssertionCreate

    payload = {
        "entity_id": "todo:sample",
        "claim": "live_verify line=(a) row",
        "confidence": "confirmed",
        "evidence": "live_verify verdict probe",
        "attributes": {"kind": "live_verify"},
        "seeded_by": "pipeline:live-verify",
        "derivation_type": "agent_observation",
    }
    rejected = validate_assertion(AssertionCreate(**{**payload, "derivation_type": None}))
    assert rejected.rejected
    assert any(
        "derivation_type is required" in item.message for item in rejected.hard_reject
    )
    accepted = validate_assertion(AssertionCreate(**payload))
    assert not any(item.field == "derivation_type" for item in accepted.hard_reject)


def test_both_reason_and_probe_error_survive_on_relation() -> None:
    snapshot = {
        "services": [
            {
                "service": "stargate",
                "code_ref_validation": {
                    "liveness": {
                        "answer": "unknown",
                        "relation": None,
                        "reason": "probe unreachable or returned no payload",
                        "observation": {"probe_error": "HTTP timeout"},
                    }
                },
            }
        ]
    }
    relations = relations_from_snapshot(snapshot, ["stargate"])
    assert relations[0]["relation_absent_reason"] == (
        "probe unreachable or returned no payload"
    )
    assert relations[0]["probe_error"] == "HTTP timeout"


@pytest.mark.asyncio
async def test_probe_is_scheduled_with_asyncio_to_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled: dict = {}

    async def _to_thread(fn, /, *args, **kwargs):
        scheduled["fn"] = fn
        scheduled["args"] = args
        scheduled["kwargs"] = kwargs
        return fn(*args, **kwargs)

    client = _Client(
        {
            "entity_get": {
                "id": "todo:sample",
                "description": "",
                "assertions": [],
            },
            "assert": {"id": 9},
        }
    )
    monkeypatch.setattr(
        "pipelines.live_verify.v1.handlers.verdict.asyncio.to_thread",
        _to_thread,
    )
    monkeypatch.setattr(
        "pipelines.live_verify.v1.handlers.verdict.make_async_client",
        lambda *_a, **_k: client,
    )
    monkeypatch.setattr(
        "pipelines.live_verify.v1.handlers.verdict.fleet_liveness",
        lambda **_k: {"error": "down"},
    )
    out = await LiveVerifyVerdictHandler().execute(
        None,
        SimpleNamespace(
            options={
                "todo_id": "todo:sample",
                "land_sha": "abc123",
                "services": ["stargate"],
                "lines": [{"line": "(a) row exists", "verdict": "LIVE_OK"}],
            }
        ),
    )
    assert out.json["ok"] is True
    from pipelines.live_verify.v1.handlers import verdict as verdict_mod

    assert scheduled["fn"] is verdict_mod.fleet_liveness
    assert scheduled["args"] == ()
    assert scheduled["kwargs"] == {
        "code_ref": "abc123",
        "services": ["stargate"],
    }


def test_claim_cites_probe_and_observed() -> None:
    text = verdict_claim(
        [
            {
                "line": "(a) one",
                "probe_call": {"method": "fleet_liveness"},
                "observed": {"answer": "yes"},
                "verdict": "LIVE_OK",
            }
        ]
    )
    parsed = json.loads(text.split("probe=", 1)[1].split("; observed=", 1)[0])
    assert parsed["method"] == "fleet_liveness"
