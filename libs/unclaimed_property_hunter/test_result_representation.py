"""Regression: non-executed searches must never present as zero-hit results."""

from __future__ import annotations

import json

from unclaimed_property_hunter.cli import _emit_run_json
from unclaimed_property_hunter.models import Query, RunRecord
from unclaimed_property_hunter.record import persist_run
from unclaimed_property_hunter.result_surface import (
    format_entity_description,
    format_operator_stderr,
    public_run_dict,
)


def _probe_record() -> RunRecord:
    return RunRecord(
        run_id="testsubject-20260814T095209Z",
        utc_timestamp="2026-08-14T09:52:09Z",
        query=Query(surname="Testsubject", intended_query_string="lastName=Testsubject"),
        run_kind="transport_probe",
        search_executed=False,
        raw_payload_uri="cortex://notes/system/unclaimed-property/runs/x.raw",
        raw_sha256="abc",
        hits=[],
        notes="turnstile blocked",
    )


def _completed_zero_hit_record() -> RunRecord:
    return RunRecord(
        run_id="testsubject-20260814T120000Z",
        utc_timestamp="2026-08-14T12:00:00Z",
        query=Query(surname="Testsubject", intended_query_string="lastName=Testsubject"),
        run_kind="ingest_json",
        search_executed=True,
        raw_payload_uri="cortex://notes/system/unclaimed-property/runs/y.raw",
        raw_sha256="def",
        hits=[],
        notes="parsed empty hits array",
    )


def test_non_executed_sidecar_hit_count_is_null_not_zero():
    payload = public_run_dict(_probe_record())
    assert payload["search_executed"] is False
    assert payload["hit_count"] is None
    assert payload["hit_count"] != 0
    assert payload["verdict"] == "NOT EXECUTED"
    assert payload["execution_block_reason"] == "requires_js_cloudflare_turnstile_sws_session"


def test_completed_zero_hit_allows_hit_count_zero():
    payload = public_run_dict(_completed_zero_hit_record())
    assert payload["search_executed"] is True
    assert payload["hit_count"] == 0
    assert payload["verdict"] == "EXECUTED ZERO"
    assert payload["execution_block_reason"] is None


def test_entity_description_never_says_hits_equals_zero_when_not_executed():
    desc = format_entity_description(_probe_record())
    assert "hits=0" not in desc
    assert "requires_js_cloudflare_turnstile_sws_session" in desc


def test_operator_stderr_names_block_reason():
    line = format_operator_stderr(_probe_record())
    assert line == (
        "SEARCH NOT EXECUTED: reason=requires_js_cloudflare_turnstile_sws_session"
    )


def test_persist_run_entity_attributes_hit_count_null_when_not_executed(monkeypatch):
    captured: dict = {}

    def fake_create_entity(**payload):
        captured.update(payload)
        return {"id": payload["id"]}

    def fake_assert_claim(**_payload):
        return {"item": {"id": "assert-1"}}

    monkeypatch.setattr(
        "unclaimed_property_hunter.record.create_entity", fake_create_entity
    )
    monkeypatch.setattr(
        "unclaimed_property_hunter.record.assert_claim", fake_assert_claim
    )
    persist_run(_probe_record())
    attrs = captured["attributes"]
    assert attrs["search_executed"] is False
    assert attrs.get("hit_count") is None
    assert attrs["execution_block_reason"] == "requires_js_cloudflare_turnstile_sws_session"


def test_cli_json_never_serializes_zero_hit_count_for_probe(capsys):
    _emit_run_json(_probe_record(), extra={"cortex": {}})
    out = capsys.readouterr()
    payload = json.loads(out.out)
    assert payload["search_executed"] is False
    assert payload["hit_count"] is None
    assert (
        out.err.strip().startswith(
            "SEARCH NOT EXECUTED: reason=requires_js_cloudflare_turnstile_sws_session"
        )
    )
