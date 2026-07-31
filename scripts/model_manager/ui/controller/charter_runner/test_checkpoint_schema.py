"""Unit tests for charter-state footer schema and materializer dual-write."""

from __future__ import annotations

import pytest

from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    append_footer_to_packet,
    emit_footer,
    EMPTY_GATED_PICKUP_SENTINEL,
    output_format_footer_requirement,
    validate_checkpoint_footer,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec import (
    materialize_resume_packet,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec import (
    materialize_autonomous_packet,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec import (
    materialize_closed_detent_packet,
)
from scripts.model_manager.ui.controller.charter_runner.window_exec import (
    materialize_consult_packet,
)

pytestmark = pytest.mark.offline

_MIN_CHECKPOINT = """\
# CHECKPOINT

## WIP / In-flight
_None this window._

## Next pickup
1. G1 — first gated step

## Steps
1. [ ] G1 — first gated step

## Frictions
_None this window._

— RESUME (any seat, no command): load agent-bus-discipline → this CHECKPOINT.
"""

_VALID_FOOTER = emit_footer(
    schema_version=1,
    status="CHECKPOINT",
    next_pickup={"gid": "G1", "lane": "judgment", "executor": "cursor-sdk"},
    wip=None,
    consult={"role": None, "poll_hint": None, "from": None},
    revise_count=0,
    evidence=[],
    window_id="charter-5998-w1",
    transition_id=None,
)


def test_valid_footer_passes_validate() -> None:
    body = f"prose\n\n{_VALID_FOOTER}"
    result = validate_checkpoint_footer(body)
    assert result.ok is True
    assert result.errors == ()


def test_missing_fence_fails_with_path() -> None:
    result = validate_checkpoint_footer("no footer here")
    assert result.ok is False
    assert "charter-state fence missing" in result.errors[0]


def test_malformed_json_names_field_path() -> None:
    body = "```charter-state\n{not json}\n```"
    result = validate_checkpoint_footer(body)
    assert result.ok is False
    assert any("malformed" in err for err in result.errors)


def test_invalid_next_pickup_gid_names_field_path() -> None:
    footer = emit_footer(
        schema_version=1,
        status="CHECKPOINT",
        next_pickup={"gid": "", "lane": "judgment", "executor": "x"},
        wip=None,
        consult={"role": None, "poll_hint": None, "from": None},
        revise_count=0,
        evidence=[],
        window_id="charter-5998-w1",
        transition_id=None,
    )
    result = validate_checkpoint_footer(footer)
    assert result.ok is False
    assert "next_pickup.gid" in result.errors


def test_append_footer_to_packet_replaces_existing() -> None:
    first = append_footer_to_packet(
        "packet body",
        schema_version=1,
        status="CHECKPOINT",
        next_pickup={"gid": "G1", "lane": "judgment", "executor": "x"},
        wip=None,
        consult={"role": None, "poll_hint": None, "from": None},
        revise_count=0,
        evidence=[],
        window_id="charter-5998-w1",
        transition_id=None,
    )
    second = append_footer_to_packet(
        first,
        schema_version=1,
        status="BLOCKED",
        next_pickup={"gid": "G2", "lane": "mechanical", "executor": "y"},
        wip=None,
        consult={"role": None, "poll_hint": None, "from": None},
        revise_count=1,
        evidence=[],
        window_id="charter-5998-w2",
        transition_id="t-1",
    )
    assert second.count("```charter-state") == 1
    assert '"status": "BLOCKED"' in second
    assert validate_checkpoint_footer(second).ok is True


@pytest.mark.parametrize(
    ("materializer", "extra_kwargs"),
    [
        (materialize_resume_packet, {}),
        (materialize_autonomous_packet, {}),
        (materialize_consult_packet, {}),
        (materialize_closed_detent_packet, {"scoreboard_uri": None}),
    ],
)
def test_materializer_body_contains_charter_state_fence(
    materializer,
    extra_kwargs: dict,
) -> None:
    parsed = parse_checkpoint(_MIN_CHECKPOINT)
    packet = materializer("5998", parsed, window_index=3, **extra_kwargs)
    assert "```charter-state" in packet
    result = validate_checkpoint_footer(packet)
    assert result.ok is True
    assert '"window_id": "charter-5998-w3"' in packet


def test_string_wip_fails_validate_naming_wip() -> None:
    """6091 shape: bare-string cross-root wip must fail-closed on field path wip."""
    footer = emit_footer(
        schema_version=1,
        status="CHECKPOINT",
        next_pickup={"gid": "G1", "lane": "judgment", "executor": "cursor-sdk"},
        wip=None,
        consult={"role": None, "poll_hint": None, "from": None},
        revise_count=0,
        evidence=[],
        window_id="charter-6091-w7",
        transition_id=None,
    )
    bad = footer.replace('"wip": null', '"wip": "charter-5975-consult@6099"')
    result = validate_checkpoint_footer(bad)
    assert result.ok is False
    assert "wip" in result.errors


def test_output_format_states_phase0_wip_null_rule() -> None:
    text = output_format_footer_requirement(window_id="charter-6110-w3")
    assert "wip to null" in text
    assert "cross-root" in text


def test_output_format_requires_none_sentinel_when_gated_pickup_empty() -> None:
    text = output_format_footer_requirement()
    assert "none" in text
    assert "never JSON null" in text


def test_validate_accepts_empty_gated_pickup_sentinel() -> None:
    footer = emit_footer(
        schema_version=1,
        status="CHECKPOINT",
        next_pickup=dict(EMPTY_GATED_PICKUP_SENTINEL),
        wip=None,
        consult={"role": None, "poll_hint": None, "from": None},
        revise_count=0,
        evidence=[],
        window_id="charter-6518-w1",
        transition_id=None,
    )
    result = validate_checkpoint_footer(footer)
    assert result.ok is True


def test_validate_rejects_null_next_pickup_gid() -> None:
    body = """```charter-state
{
  "consult": {"from": null, "poll_hint": null, "role": null},
  "evidence": [],
  "next_pickup": {"executor": "x", "gid": null, "lane": "judgment"},
  "revise_count": 0,
  "schema_version": 1,
  "status": "CHECKPOINT",
  "transition_id": null,
  "window_id": "charter-6518-w1",
  "wip": null
}
```
"""
    result = validate_checkpoint_footer(body)
    assert result.ok is False
    assert "next_pickup.gid" in result.errors
