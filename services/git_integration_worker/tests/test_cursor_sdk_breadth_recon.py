"""Tests for breadth-recon Explore-default closeout verification."""

from __future__ import annotations

import json

import pytest

from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)

from services.git_integration_worker.cursor_auto.closeout_relay_briefing import (
    finalize_relay_payload,
)
from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    CloseoutRelayPayload,
)
from services.git_integration_worker.cursor_sdk_breadth_recon import (
    amend_breadth_recon_gaps,
    breadth_recon_deviation,
    packet_owes_breadth_recon,
)
from services.git_integration_worker.cursor_sdk_packet import resolve_prompt_preamble
from services.git_integration_worker.cursor_sdk_subagent_capture import (
    SUBAGENTS_SURFACE,
)


def test_packet_preamble_includes_breadth_recon_block() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="implement",
        prompt_preamble=None,
        inferred_contract=None,
    )
    assert "BREADTH RECON — EXPLORE DEFAULT" in preamble
    assert 'Task(subagent_type="explore"' in preamble
    assert "recon_method:" in preamble


@pytest.mark.parametrize(
    ("contract", "expected"),
    [
        ("investigate", True),
        ("light-bounded", True),
        ("implement", False),
        ("pure-mechanical", False),
    ],
)
def test_packet_owes_breadth_recon_by_contract(contract: str, expected: bool) -> None:
    assert packet_owes_breadth_recon(contract=contract) is expected


def test_breadth_recon_deviation_when_investigate_without_explore() -> None:
    wrapper = json.dumps(
        {
            "contract": "investigate",
            "effects_manifest": {
                "dispatch_id": "d1",
                "thread_id": "t1",
                "surfaces": {
                    SUBAGENTS_SURFACE: {
                        "surface": SUBAGENTS_SURFACE,
                        "source": "stream",
                        "entries": [],
                        "authority_class": "observed",
                        "absence_semantics": "absence=zero",
                    }
                },
            },
        }
    )
    assert (
        breadth_recon_deviation(
            body="status: complete\nac_verdict: done",
            wrapper_text=wrapper,
        )
        == "recon:breadth_explore_not_used"
    )


def test_breadth_recon_suppressed_when_explore_used() -> None:
    manifest = EffectsManifest(
        dispatch_id="d1",
        thread_id="t1",
        surfaces={
            SUBAGENTS_SURFACE: SurfaceSection(
                surface=SUBAGENTS_SURFACE,
                source="stream",
                entries=[
                    EffectEntry(op="Task", target="explore", identity="c1"),
                ],
                authority_class="observed",
                absence_semantics="absence=zero",
            )
        },
    )
    wrapper = json.dumps({"contract": "investigate", "effects_manifest": manifest.model_dump()})
    assert (
        breadth_recon_deviation(body="status: complete", wrapper_text=wrapper) is None
    )


def test_breadth_recon_suppressed_when_recon_method_documented() -> None:
    wrapper = json.dumps({"contract": "investigate", "effects_manifest": {}})
    body = "status: complete\nrecon_method: in-seat — loci known from packet path list"
    assert breadth_recon_deviation(body=body, wrapper_text=wrapper) is None


def test_amend_breadth_recon_is_advisory_does_not_clamp_status() -> None:
    wrapper = json.dumps({"contract": "investigate", "effects_manifest": {}})
    payload = amend_breadth_recon_gaps(
        "status: complete",
        status="complete",
        source="section2_bus",
        wrapper_text=wrapper,
    )
    assert payload.status == "complete"
    assert "recon:breadth_explore_not_used" in payload.body


def test_finalize_relay_includes_breadth_recon_deviation() -> None:
    wrapper = json.dumps({"contract": "investigate", "effects_manifest": {}})
    result = finalize_relay_payload(
        CloseoutRelayPayload(body="status: complete", status="complete", source="wrapper"),
        wrapper_text=wrapper,
    )
    assert "recon:breadth_explore_not_used" in result.body
