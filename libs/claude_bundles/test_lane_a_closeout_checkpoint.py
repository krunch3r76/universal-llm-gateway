"""Unit tests for lane-A CLOSEOUT checkpoint fail-closed gate."""

from __future__ import annotations

import pytest

from claude_bundles.lane_a_closeout_checkpoint import (
    LANE_A_CHECKPOINT_FIX_HINT,
    is_lane_a_closeout,
    refusal_envelope,
    validate_lane_a_closeout_checkpoint,
)

pytestmark = pytest.mark.offline


def test_missing_checkpoint_refused() -> None:
    body = "TYPE: CLOSEOUT\nstatus: complete\n\n| status | complete |"
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    assert verdict.ok is False
    assert verdict.reason == "lane_a_checkpoint_missing"
    assert verdict.missed_tokens == ("checkpoint:",)
    env = refusal_envelope(verdict)
    assert env["status"] == "blocked"
    assert env["fix_hint"] == LANE_A_CHECKPOINT_FIX_HINT


def test_deferred_passes() -> None:
    body = (
        "TYPE: CLOSEOUT\n"
        "status: complete\n"
        "checkpoint: deferred: foreign WIP on shared checkout\n"
    )
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    assert verdict.ok is True
    assert verdict.checkpoint_value == "deferred: foreign WIP on shared checkout"


def test_committed_passes() -> None:
    body = (
        "TYPE: CLOSEOUT\n"
        "checkpoint: committed abc1234 paths=3\n"
    )
    assert validate_lane_a_closeout_checkpoint(body=body).ok is True


def test_committed_with_pending_passes() -> None:
    """Row 18 — mixed committed+pending token must clear the fail-closed gate."""
    body = (
        "TYPE: CLOSEOUT\n"
        "checkpoint: committed abc1234 paths=3 (+2 pending)\n"
    )
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    assert verdict.ok is True
    assert verdict.checkpoint_value == "committed abc1234 paths=3 (+2 pending)"


def test_nothing_authored_passes() -> None:
    body = "TYPE: CLOSEOUT\ncheckpoint: nothing_authored\n"
    assert validate_lane_a_closeout_checkpoint(body=body).ok is True


def test_authored_cortex_single_passes() -> None:
    """Row 19 — cortex-only token clears the fail-closed gate."""
    digest = "a" * 64
    body = (
        "TYPE: CLOSEOUT\n"
        f"checkpoint: authored_cortex: cortex://notes/system/a.md {digest}\n"
    )
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    assert verdict.ok is True
    assert verdict.checkpoint_value == (
        f"authored_cortex: cortex://notes/system/a.md {digest}"
    )


def test_authored_cortex_multi_semicolon_passes() -> None:
    d1 = "b" * 64
    d2 = "c" * 64
    body = (
        "TYPE: CLOSEOUT\n"
        "checkpoint: authored_cortex: "
        f"cortex://notes/a.md {d1}; cortex://notes/b.md {d2}\n"
    )
    assert validate_lane_a_closeout_checkpoint(body=body).ok is True


def test_authored_cortex_rejects_short_digest() -> None:
    body = (
        "TYPE: CLOSEOUT\n"
        "checkpoint: authored_cortex: cortex://notes/a.md deadbeef\n"
    )
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    assert verdict.ok is False
    assert verdict.reason == "lane_a_checkpoint_malformed"


def test_authored_cortex_rejects_workspaces_uri() -> None:
    digest = "d" * 64
    body = (
        "TYPE: CLOSEOUT\n"
        f"checkpoint: authored_cortex: workspaces://repo/tmp/x.md {digest}\n"
    )
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    assert verdict.ok is False
    assert verdict.reason == "lane_a_checkpoint_malformed"


def test_malformed_checkpoint_refused() -> None:
    body = "TYPE: CLOSEOUT\ncheckpoint: committed-but-no-sha\n"
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    assert verdict.ok is False
    assert verdict.reason == "lane_a_checkpoint_malformed"


def test_non_closeout_passes_without_checkpoint() -> None:
    assert is_lane_a_closeout(body="TYPE: DIRECTIVE\ncontract: implement\n") is False
    assert validate_lane_a_closeout_checkpoint(
        body="TYPE: DIRECTIVE\ncontract: implement\n"
    ).ok is True


def test_malformed_doubled_checkpoint_normalizes() -> None:
    from claude_bundles.lane_a_closeout_checkpoint import normalize_checkpoint_value

    raw = "checkpoint: committed f230fa040476144e73827520ee5a78d470a24107 paths=5"
    body = f"TYPE: CLOSEOUT\ncheckpoint: `{raw}`\n"
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    assert verdict.ok, verdict.reason
    assert normalize_checkpoint_value(f"`{raw}`") == (
        "committed f230fa040476144e73827520ee5a78d470a24107 paths=5"
    )


def test_normalize_checkpoint_value_strips_checkpoint_claim_prefix() -> None:
    from claude_bundles.lane_a_closeout_checkpoint import normalize_checkpoint_value

    assert normalize_checkpoint_value("checkpoint_claim: nothing_authored") == (
        "nothing_authored"
    )
    assert normalize_checkpoint_value("`checkpoint_claim: nothing_authored`") == (
        "nothing_authored"
    )


def test_plane_qualified_checkpoint_tokens_legal() -> None:
    """closeout-plane-legibility — @plane infix is additive, not a malform."""
    bodies = [
        "TYPE: CLOSEOUT\ncheckpoint: committed@local-master abcdef1 paths=2\n",
        "TYPE: CLOSEOUT\ncheckpoint: deferred@local-master: waiting on land\n",
        "TYPE: CLOSEOUT\ncheckpoint: nothing_authored@local-master\n",
        (
            "TYPE: CLOSEOUT\n"
            "checkpoint: authored_cortex@local-master: "
            "cortex://notes/system/x.md "
            + ("a" * 64)
            + "\n"
        ),
    ]
    for body in bodies:
        verdict = validate_lane_a_closeout_checkpoint(body=body)
        assert verdict.ok, (body, verdict.reason)


def test_baseline_unavailable_passes() -> None:
    body = (
        "TYPE: CLOSEOUT\n"
        "checkpoint: baseline_unavailable: no admit baseline recorded for this dispatch\n"
    )
    verdict = validate_lane_a_closeout_checkpoint(body=body)
    assert verdict.ok is True
    assert verdict.checkpoint_value == (
        "baseline_unavailable: no admit baseline recorded for this dispatch"
    )


def test_baseline_unavailable_plane_qualified_passes() -> None:
    body = (
        "TYPE: CLOSEOUT\n"
        "checkpoint: baseline_unavailable@local-master: "
        "no admit baseline recorded for this dispatch\n"
    )
    assert validate_lane_a_closeout_checkpoint(body=body).ok is True
