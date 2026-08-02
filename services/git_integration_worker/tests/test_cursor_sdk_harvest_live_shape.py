"""Item 18 / AC-H — harvest live stream MCP content[] result shape (attempt 9).

Uses the captured fixture ``item18_attempt9_live_obs_result.json`` only.
The falsified 2709-byte byte-padded reconstruction was quarantined — see
``fixtures/quarantine/README.md``.
"""

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
from services.git_integration_worker.cursor_sdk_toolcall_retention import (
    RESULT_BODY_PRESENT,
    harvest_result_from_observation,
    hydrate_tool_calls_for_boundary_harvest,
)
from services.git_integration_worker.cursor_sdk_boundary_finalize import (
    finalize_boundary_manifest,
)

pytestmark = pytest.mark.offline

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "item18_attempt9_live_obs_result.json"
_LIVE_ENTITY = "todo:ac9g-live-falsifier"
_LIVE_CALL_ID = "tool_168be023-91f8-47a3-a61b-f85a9ff0e23"
_LIVE_ASSERTION_ID = 27489
_LIVE_DISPATCH = "4ca3bed41023-b609e179"


def _load_live_obs_result_body() -> dict[str, object]:
    if not _FIXTURE.is_file():
        pytest.fail(f"missing captured fixture: {_FIXTURE}")
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


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


def test_ac_h1_captured_fixture_unwraps_to_assertion_id() -> None:
    body = _load_live_obs_result_body()
    payload = unwrap_tool_result(body)
    assert isinstance(payload, dict)
    assert payload.get("item", {}).get("id") == _LIVE_ASSERTION_ID
    assert "_next" in payload
    assert "write_discipline" in payload


def test_ac_h2_harvest_resolves_assertion_from_live_content_array_shape() -> None:
    body = _load_live_obs_result_body()
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


def test_ac_h4_h5_captured_fixture_clears_divergence_and_assertion_identity() -> None:
    body = _load_live_obs_result_body()
    obs = _live_stream_obs(result=body)
    merged = merge_stream_cortex_entries(_conversation_manifest(), (obs,))
    assert merged is not None
    entry = merged.surfaces["cortex"].entries[0]
    assert entry.identity == f"assertion:{_LIVE_ASSERTION_ID}"
    assert harvest_cortex_assertion_ids(merged) == [str(_LIVE_ASSERTION_ID)]
    _, divs = reconcile_observed_vs_committed(merged, (obs,))
    assert divs == []


def test_ac_w3_retained_body_wires_to_boundary_finalize_without_stream_result() -> None:
    """Consumption path: result_body reaches merge at closeout when stream result is absent."""
    body = _load_live_obs_result_body()
    obs = _live_stream_obs(result=None)
    obs = ToolCallObservation(
        call_id=obs.call_id,
        tool_name=obs.tool_name,
        status=obs.status,
        arg_bytes=obs.arg_bytes,
        result_bytes=_json_bytes(body),
        truncated_fields=obs.truncated_fields,
        args=obs.args,
        result=None,
        result_body=body,
        result_body_status=RESULT_BODY_PRESENT,
    )
    assert harvest_result_from_observation(obs) == body
    hydrated = hydrate_tool_calls_for_boundary_harvest((obs,))
    assert hydrated[0].result == body
    finalized, divs = finalize_boundary_manifest(
        _conversation_manifest(),
        tool_calls=hydrated,  # type: ignore[arg-type]
    )
    assert finalized is not None
    entry = finalized.surfaces["cortex"].entries[0]
    assert entry.identity == f"assertion:{_LIVE_ASSERTION_ID}"
    assert divs == []
