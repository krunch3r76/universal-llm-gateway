"""Tests for frontier.sdk.closeout.partial_work.production_specimen wake instrument."""

from __future__ import annotations

import json

from services.git_integration_worker.relay.closeout_status_polarity import (
    annotate_status_claim_discrepancy,
    merge_plane_discrepancy_markers,
    status_claim_is_polysemous_partial_legend,
)


def _checkpoint_body(status: str, *, claim: str = "partial") -> str:
    return (
        f"TYPE: CLOSEOUT\n"
        f"status: {status}\n"
        f"checkpoint: nothing_authored\n\n"
        f"## §2 closeout\n\n"
        f"**status_claim:** {claim}\n\n"
        f"**checkpoint_claim:** nothing_authored\n"
    )


def _partial_capture_body() -> str:
    return (
        "TYPE: CLOSEOUT\n"
        "status: partial:capture\n"
        "checkpoint: nothing_authored\n\n"
        "## §2 closeout\n\n"
        "**status_claim:** partial\n\n"
        "**checkpoint_claim:** nothing_authored\n"
    )


def _complete_body() -> str:
    return (
        "TYPE: CLOSEOUT\n"
        "status: complete\n"
        "checkpoint: nothing_authored\n\n"
        "## §2 closeout\n\n"
        "**status_claim:** complete\n\n"
        "**checkpoint_claim:** nothing_authored\n"
    )














def test_complete_x_partial_work_emits_plane_discrepancy_not_legend() -> None:
    """AC4(a) — complete×partial:work is plane-discrepancy after a:35821 reclass."""
    marker = annotate_status_claim_discrepancy(
        claim="complete",
        measurement="partial:work",
    )
    assert marker == "status_claim@§2 complete while status@infra partial:work"
    assert merge_plane_discrepancy_markers(marker) is not None
    assert (
        status_claim_is_polysemous_partial_legend(
            claim="complete",
            measurement="partial:work",
        )
        is False
    )






def _partial_work_sidecar() -> str:
    structured = json.dumps(
        {
            "schema_version": 1,
            "status": "partial",
            "status_incomplete_class": "work",
            "work_outcome": "checks_failed",
            "capture_status": "partial",
        }
    )
    return (
        "TYPE: CLOSEOUT\n"
        "status: partial:work\n"
        "checkpoint: nothing_authored\n\n"
        "## §2 closeout\n\n"
        "**status_claim:** partial\n\n"
        "**checkpoint_claim:** nothing_authored\n\n"
        f"## structured_closeout_full\n\n{structured}"
    )




