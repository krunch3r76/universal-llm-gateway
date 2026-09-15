"""Tests for shared harvest/provenance registration-drift fields."""

from __future__ import annotations

import inspect

import pytest

from claude_bundles.cse_identity_drift import (
    build_harvest_identity_block,
    evaluate_harvest_identity,
    registration_drift_fields,
)
from claude_bundles.cse_provenance import ProvenanceEpisode
from claude_bundles.cse_provenance_resolve import resolve as resolve_provenance

pytestmark = pytest.mark.offline


def test_registration_drift_fields_byte_identical_across_call_sites() -> None:
    drift = registration_drift_fields("req-reg", "cur-reg")
    harvest_block = build_harvest_identity_block(
        requested_chat_url="https://claude.ai/cowork/cse_x",
        requested_registration_id=drift["requested_registration_id"],
        observed_chat_url="https://claude.ai/cowork/cse_x",
        current_registration_id=drift["current_registration_id"],
        identity_check="registration_drift",
    )
    assert harvest_block["requested_registration_id"] == drift["requested_registration_id"]
    assert harvest_block["current_registration_id"] == drift["current_registration_id"]

    episode = ProvenanceEpisode(
        episode_id="ep-1",
        registration_id="cur-reg",
        chat_url="https://claude.ai/cowork/cse_conflict",
        cdp_url="http://127.0.0.1:9222",
        lane_thread="10479",
        parent_thread="10479",
        lane_role="root",
        state="current",
        evidence_class="observed",
        attribution_source="registry",
        correlation_id="exec-1",
        observed_at=1.0,
        lineage_state="proven",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "claude_bundles.cse_provenance_resolve.read_episodes",
            lambda: [episode],
        )
        mp.setattr(
            "claude_bundles.cse_provenance_resolve.is_row_present",
            lambda _rid: True,
        )
        conflict = resolve_provenance(
            chat_url="https://claude.ai/cowork/cse_conflict",
            registration_id="req-reg",
            host_listable=lambda _rid: True,
        )
    assert conflict["state"] == "conflict"
    assert conflict["requested_registration_id"] == drift["requested_registration_id"]
    assert conflict["current_registration_id"] == drift["current_registration_id"]
    assert "registration_drift_fields" in inspect.getsource(resolve_provenance)


def test_evaluate_harvest_identity_foreign_transcript() -> None:
    identity = evaluate_harvest_identity(
        requested_chat_url="https://claude.ai/cowork/cse_requested",
        requested_registration_id="req-reg",
        observed_chat_url="https://claude.ai/cowork/cse_observed",
        current_registration_id="cur-reg",
    )
    assert identity["identity_check"] == "foreign_transcript"


def test_evaluate_harvest_identity_registration_drift() -> None:
    cse = "https://claude.ai/cowork/cse_same"
    identity = evaluate_harvest_identity(
        requested_chat_url=cse,
        requested_registration_id="req-reg",
        observed_chat_url=cse,
        current_registration_id="cur-reg",
    )
    assert identity["identity_check"] == "registration_drift"


def test_evaluate_harvest_identity_unverified_without_page() -> None:
    identity = evaluate_harvest_identity(
        requested_chat_url="https://claude.ai/cowork/cse_x",
        requested_registration_id="req-reg",
        observed_chat_url=None,
        current_registration_id="req-reg",
    )
    assert identity["identity_check"] == "unverified"
