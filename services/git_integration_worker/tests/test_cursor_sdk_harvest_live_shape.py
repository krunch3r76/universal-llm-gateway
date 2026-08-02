"""Item 18 / AC-H — harvest live stream MCP content[] result shape (attempt 9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)

from services.git_integration_worker.cursor_sdk_cortex_identity import (
    assertion_id_from_cortex_observation,
    enrich_cortex_identities_from_stream,
    merge_stream_cortex_entries,
)
from services.git_integration_worker.cursor_sdk_manifest import harvest_cortex_assertion_ids
from services.git_integration_worker.cursor_sdk_observed_reconcile import (
    reconcile_observed_vs_committed,
)
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
    _json_bytes,
)
from services.git_integration_worker.cursor_sdk_tool_result import unwrap_tool_result

pytestmark = pytest.mark.offline

_LIVE_ENTITY = "todo:ac9g-live-falsifier"
_LIVE_CALL_ID = "tool_168be023-91f8-47a3-a61b-f85a9ff0e23"
_LIVE_ASSERTION_ID = 27489
_LIVE_DISPATCH = "4ca3bed41023-b609e179"
_LIVE_RESULT_BYTES = 2709


def _attempt9_assert_payload(*, next_pad: int) -> dict[str, object]:
    """MCP mirror body for assertion 27489; pad ``_next`` to match live result_bytes."""
    return {
        "was_new": True,
        "item": {
            "id": _LIVE_ASSERTION_ID,
            "entity_id": _LIVE_ENTITY,
            "claim": "AC-18a-LIVE-9 nested child assertion item-18 attempt 9",
            "confidence": "confirmed",
            "confidence_score": None,
            "evidence": "live nested cursor-sdk dispatch item-18 attempt-9 on GIW 2c9491b9",
            "evidence_uris": None,
            "seeded_by": None,
            "derivation_type": "agent_observation",
            "chunk_id": None,
            "chunk_id_schema": None,
            "reasoning_summary": (
                "production proof child for parent envelope cortex_assertions harvest item-18 attempt-9"
            ),
            "is_atomic": True,
            "is_decontextualized": True,
            "observed_at": "2026-08-02T04:20:04.039741+00:00",
            "valid_from": None,
            "valid_until": None,
            "superseded_by": None,
            "review_status": None,
            "reviewer": None,
            "reviewed_at": None,
            "review_notes": None,
            "resolution_status": None,
            "fulfillment_assertion_id": None,
            "quality_score": 0.76,
            "prospective_summary": (
                "What downstream validation steps depend on the success of AC-18a-LIVE-9? "
                "What failure modes in item-18 could invalidate this nested assertion?"
            ),
            "events_json": "[]",
            "artifact_uri": None,
            "artifact_storage": "inline",
            "entrenchment_score": 0.6408,
            "predicate_form": "part_of(todo:ac9g-live-falsifier, AC-18a-LIVE-9)",
            "created_at": "2026-08-02 04:20:04",
            "raw_predicate_form": "part_of(todo:ac9g-live-falsifier, AC-18a-LIVE-9)",
            "normalization_decision": "no_match",
            "candidate_set_fingerprint": "cbe5cfdf7c2118a9",
            "normalizer_version": "v1.3.1",
            "attributes": None,
        },
        "dry_run": False,
        "would_write": None,
        "near_duplicate_warning": None,
        "validation_warnings": None,
        "contradiction_warnings": None,
        "predicate_form_normalize": None,
        "already_known": False,
        "known_state_reason": None,
        "matched_assertion_id": None,
        "_next": "n" * next_pad,
        "write_discipline": {
            "level": "warn",
            "reasons": ["similar_existing_claims"],
            "message": "write-discipline advisory (non-blocking)",
            "suggestions": [
                "4 similar claim(s) on entity (top sim=1.00, assertion #27488) — call analyze_impact before assert"
            ],
            "entity_stats": {"entity_type": "todo", "assertion_count": 7, "relationship_count": 0},
            "analyze_impact": {"touched_count": 4, "top_similarity": 1.0},
            "_thresholds": {"supersede_similarity": 0.85, "touched_similarity": 0.72},
        },
    }


def _live_obs_result_body() -> dict[str, object]:
    for pad in range(800):
        text = json.dumps(
            _attempt9_assert_payload(next_pad=pad),
            separators=(",", ":"),
            ensure_ascii=False,
        )
        result = {"content": [{"type": "text", "text": text}]}
        if _json_bytes(result) == _LIVE_RESULT_BYTES:
            return result
    pytest.fail(f"could not synthesize live obs.result with result_bytes={_LIVE_RESULT_BYTES}")


def _conversation_manifest() -> EffectsManifest:
    return EffectsManifest(
        dispatch_id=_LIVE_DISPATCH,
        thread_id="6674",
        capture_sources=["conversation", "wrapper"],
        surfaces={
            "cortex": SurfaceSection(
                surface="cortex",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="cortex",
                        target=_LIVE_ENTITY,
                        identity=_LIVE_ENTITY,
                        detail={
                            "providerIdentifier": "user-vortex",
                            "toolName": "cortex",
                            "args": {
                                "tool": "assert",
                                "arguments": json.dumps(
                                    {
                                        "entity_id": _LIVE_ENTITY,
                                        "claim": "AC-18a-LIVE-9 nested child assertion item-18 attempt 9",
                                        "confidence": "confirmed",
                                        "evidence": (
                                            "live nested cursor-sdk dispatch item-18 attempt-9 on GIW 2c9491b9"
                                        ),
                                        "derivation_type": "agent_observation",
                                        "reasoning_summary": (
                                            "production proof child for parent envelope "
                                            "cortex_assertions harvest item-18 attempt-9"
                                        ),
                                    }
                                ),
                            },
                        },
                    )
                ],
            )
        },
    )


def _live_stream_obs(*, result: object) -> ToolCallObservation:
    return ToolCallObservation(
        call_id=_LIVE_CALL_ID,
        tool_name="cortex",
        status="completed",
        arg_bytes=495,
        result_bytes=_json_bytes(result),
        truncated_fields=(),
        args={
            "providerIdentifier": "user-vortex",
            "toolName": "cortex",
            "args": {
                "tool": "assert",
                "arguments": json.dumps(
                    {
                        "entity_id": _LIVE_ENTITY,
                        "claim": "AC-18a-LIVE-9 nested child assertion item-18 attempt 9",
                        "confidence": "confirmed",
                        "evidence": "live nested cursor-sdk dispatch item-18 attempt-9 on GIW 2c9491b9",
                        "derivation_type": "agent_observation",
                        "reasoning_summary": (
                            "production proof child for parent envelope cortex_assertions harvest item-18 attempt-9"
                        ),
                    }
                ),
            },
        },
        result=result,
    )


def test_ac_h1_live_obs_result_body_matches_event_fingerprint() -> None:
    body = _live_obs_result_body()
    assert _json_bytes(body) == _LIVE_RESULT_BYTES
    payload = unwrap_tool_result(body)
    assert isinstance(payload, dict)
    assert payload.get("item", {}).get("id") == _LIVE_ASSERTION_ID


def test_ac_h2_harvest_resolves_assertion_from_live_content_array_shape() -> None:
    body = _live_obs_result_body()
    obs = _live_stream_obs(result=body)
    assert assertion_id_from_cortex_observation(obs) == _LIVE_ASSERTION_ID


def test_ac_h3_stream_label_on_observation_when_harvest_fails() -> None:
    unparseable = {"content": [{"type": "text", "text": "not-json-boundary-ack"}]}
    obs = _live_stream_obs(result=unparseable)
    manifest = _conversation_manifest()
    enriched = enrich_cortex_identities_from_stream(manifest, (obs,))
    assert enriched is not None
    assert "stream" in enriched.capture_sources
    assert enriched.surfaces["cortex"].entries[0].identity == _LIVE_ENTITY


def test_ac_h4_h5_failing_run_fixture_clears_divergence_and_assertion_identity() -> None:
    body = _live_obs_result_body()
    obs = _live_stream_obs(result=body)
    merged = merge_stream_cortex_entries(_conversation_manifest(), (obs,))
    assert merged is not None
    entry = merged.surfaces["cortex"].entries[0]
    assert entry.identity == f"assertion:{_LIVE_ASSERTION_ID}"
    assert harvest_cortex_assertion_ids(merged) == [str(_LIVE_ASSERTION_ID)]
    _, divs = reconcile_observed_vs_committed(merged, (obs,))
    assert divs == []


def test_fixture_file_roundtrip_if_present() -> None:
    path = Path(__file__).resolve().parent / "fixtures" / "item18_attempt9_live_obs_result.json"
    if not path.is_file():
        pytest.skip("optional serialized fixture absent")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert unwrap_tool_result(loaded).get("item", {}).get("id") == _LIVE_ASSERTION_ID
