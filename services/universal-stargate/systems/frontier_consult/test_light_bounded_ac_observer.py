"""Unit tests for light-bounded AC observer predicate and resolution."""

from __future__ import annotations

import pytest

from systems.frontier_consult.generate_admission_context_store import (
    read_admission_context,
    reset_generate_admission_stores_for_tests,
    write_admission_context,
)
from systems.frontier_consult.light_bounded_ac_observer import (
    _PATH_SIM_ADMIT_GATE,
    GENERATE_LANE_AC_OBSERVER_FOOTER,
    instruction_mentions_production_code,
    normalize_dispatch_lane,
    planning_review_spawn_suppressed,
    prepare_lb_auto_review_for_generate,
    resolve_auto_review_child,
    resolve_dispatch_lane_for_generate,
    stamp_lb_review_spawn_fields,
)
from systems.frontier_consult.packet_review_surface import (
    FOOTER_BY_SURFACE,
    NEGATIVE_SPACE_BY_SURFACE,
    SELF_CHECK_NON_AUTHORITY_LINE,
    classify_review_surface,
)
from systems.frontier_consult.review_child_spawn_hook import (
    _build_generate_lane_review_prompt,
    should_spawn_review_child,
)


@pytest.fixture(autouse=True)
def _clean_stores() -> None:
    reset_generate_admission_stores_for_tests()
    yield
    reset_generate_admission_stores_for_tests()


@pytest.mark.parametrize(
    ("text", "packet_path", "expected"),
    [
        ("touch services/foo.py", None, True),
        ("under libs/bar", None, True),
        ("docs/readme only", None, False),
        (None, "tasks/packets/services/widget.md", True),
        (None, "tasks/packets/libs/widget.md", True),
        (None, "tasks/packets/docs/widget.md", False),
        ("", "tasks/packets/services/widget.md", True),
    ],
)
def test_instruction_mentions_production_code(
    text: str | None,
    packet_path: str | None,
    expected: bool,
) -> None:
    assert instruction_mentions_production_code(text, packet_path) is expected


@pytest.mark.parametrize(
    (
        "contract",
        "inbound",
        "packet_text",
        "message_text",
        "packet_path",
        "effective",
        "defaulted",
    ),
    [
        # inbound=None ⇒ caller expressed no preference; the default may fill.
        ("light-bounded", None, "services/foo.py", None, None, True, True),
        ("light-bounded", None, None, "edit libs/bar", None, True, True),
        ("light-bounded", None, None, None, "packets/services/x.md", True, True),
        ("light-bounded", None, "docs only", None, None, False, False),
        ("light-bounded", None, "", None, "packets/libs/x.md", True, True),
        ("light-bounded", True, "services/foo.py", None, None, True, False),
        # inbound=False is an explicit caller opt-out and outranks the default.
        ("light-bounded", False, "services/foo.py", None, None, False, False),
        ("light-bounded", False, None, None, "packets/services/x.md", False, False),
        ("light-bounded", False, "docs only", None, None, False, False),
        ("implement", None, "services/foo.py", None, None, False, False),
        ("implement", False, "services/foo.py", None, None, False, False),
        ("pure-mechanical", None, "services/foo.py", None, None, False, False),
    ],
)
def test_resolve_auto_review_child_matrix(
    contract: str,
    inbound: bool | None,
    packet_text: str | None,
    message_text: str | None,
    packet_path: str | None,
    effective: bool,
    defaulted: bool,
) -> None:
    got_effective, got_defaulted = resolve_auto_review_child(
        contract=contract,
        auto_review_child=inbound,
        packet_text=packet_text,
        message_text=message_text,
        packet_path=packet_path,
    )
    assert got_effective is effective
    assert got_defaulted is defaulted


@pytest.mark.parametrize(
    ("inbound", "effective", "defaulted"),
    [(None, True, True), (False, False, False), (True, True, False)],
)
def test_prepare_lb_auto_review_honors_explicit_opt_out(
    inbound: bool | None,
    effective: bool,
    defaulted: bool,
) -> None:
    """The generate entrypoint must not let the default outrank a caller False."""
    got_effective, got_defaulted, _ = prepare_lb_auto_review_for_generate(
        contract="light-bounded",
        auto_review_child=inbound,
        packet_path=None,
        message_text="edit services/foo.py",
    )
    assert got_effective is effective
    assert got_defaulted is defaulted


def test_packet_body_precedes_message_for_predicate() -> None:
    effective, defaulted = resolve_auto_review_child(
        contract="light-bounded",
        auto_review_child=None,
        packet_text="docs/readme",
        message_text="services/foo.py",
        packet_path=None,
    )
    assert effective is False
    assert defaulted is False


@pytest.mark.asyncio
async def test_advisory_lifecycle_forced_flag_spawn_and_footer() -> None:
    effective, defaulted = resolve_auto_review_child(
        contract="light-bounded",
        auto_review_child=None,
        packet_text="Files: services/foo/bar.py",
        message_text=None,
        packet_path="tasks/packets/lb.md",
    )
    assert effective is True
    assert defaulted is True

    write_admission_context(
        execution_id="exec-lb-ac",
        auto_review_child=effective,
        op="generate",
        role="cursor-sdk",
        resolved_model="cursor/claude-sonnet-5",
        parent_dispatch_thread_id="thread:parent",
        dispatch_thread_id="thread:parent",
    )
    ctx = read_admission_context("exec-lb-ac")
    assert ctx is not None
    assert should_spawn_review_child(ctx) is True

    prompt = await _build_generate_lane_review_prompt(
        request_id="req-ac",
        parent_dispatch_thread_id="thread:parent",
    )
    assert "Self-check PASS is evidence to inspect, not completion authority" in prompt
    assert "PASS or FAIL per packet AC" in prompt


_SIDECAR_PACKET = """
<scope>
Planning-only sidecar packet.
Files expected: - `cortex://notes/system/specs/foo.md`
</scope>
<task_guidance>
Write deliverable to cortex://notes/system/specs/foo.md
STOP after A sidecar — ¬ implement.
</task_guidance>
<output_format>
fs(op="write", path="cortex://notes/system/specs/foo.md")
</output_format>
<mcp_capabilities>
fs(op="write", path="cortex://notes/system/specs/foo.md")
</mcp_capabilities>
<corpus>
Code under services/universal-stargate/systems/frontier_consult/
</corpus>
"""

_SOURCE_PACKET = """
<scope>
Files expected: - `services/universal-stargate/systems/foo.py`
</scope>
"""

_WORKSPACES_SOURCE_PACKET = """
<scope>
Files expected: - `workspaces://universal-llm-gateway/services/foo.py`
</scope>
"""

_HYBRID_PACKET = """
<scope>
Files expected: - `services/universal-stargate/systems/foo.py`
</scope>
<task_guidance>
Write deliverable to cortex://notes/system/specs/foo.md
STOP after A sidecar — ¬ implement.
</task_guidance>
<output_format>
fs(op="write", path="cortex://notes/system/specs/foo.md")
</output_format>
"""


@pytest.mark.parametrize(
    ("packet_text", "expected"),
    [
        (_SIDECAR_PACKET, "sidecar"),
        (_SOURCE_PACKET, "source"),
        (_WORKSPACES_SOURCE_PACKET, "source"),
        (_HYBRID_PACKET, "source"),
        ("", "source"),
    ],
)
def test_classify_review_surface_matrix(packet_text: str, expected: str) -> None:
    assert classify_review_surface(packet_text) == expected


def test_self_check_line_shared_by_both_footers() -> None:
    assert SELF_CHECK_NON_AUTHORITY_LINE in FOOTER_BY_SURFACE["source"]
    assert SELF_CHECK_NON_AUTHORITY_LINE in FOOTER_BY_SURFACE["sidecar"]
    assert SELF_CHECK_NON_AUTHORITY_LINE in GENERATE_LANE_AC_OBSERVER_FOOTER
    assert FOOTER_BY_SURFACE["source"].count(SELF_CHECK_NON_AUTHORITY_LINE) == 1
    assert FOOTER_BY_SURFACE["sidecar"].count(SELF_CHECK_NON_AUTHORITY_LINE) == 1


def test_sidecar_classifies_despite_corpus_services_citation() -> None:
    assert "services/" in _SIDECAR_PACKET
    assert classify_review_surface(_SIDECAR_PACKET) == "sidecar"


def test_planning_negative_space_differs_from_source_default() -> None:
    assert NEGATIVE_SPACE_BY_SURFACE["sidecar"] != NEGATIVE_SPACE_BY_SURFACE["source"]
    assert "source/tests N/A" in NEGATIVE_SPACE_BY_SURFACE["sidecar"]
    assert "with a test?" in NEGATIVE_SPACE_BY_SURFACE["source"]


@pytest.mark.parametrize(
    ("lane", "expected"),
    [
        ("path-sim-admit-gate", "path-sim-admit-gate"),
        ("path-sim-foo-bar", "path-sim-admit-gate"),
        ("cursor-implement", "cursor-implement"),
        (None, None),
        ("", None),
    ],
)
def test_normalize_dispatch_lane(lane: str | None, expected: str | None) -> None:
    assert normalize_dispatch_lane(lane) == expected


def test_planning_review_spawn_suppressed_sidecar_stage_a() -> None:
    assert (
        planning_review_spawn_suppressed(
            contract="light-bounded",
            review_surface="sidecar",
            dispatch_lane=None,
            packet_text=_SIDECAR_PACKET,
        )
        is True
    )


def test_planning_review_spawn_suppressed_source_surface() -> None:
    assert (
        planning_review_spawn_suppressed(
            contract="light-bounded",
            review_surface="source",
            dispatch_lane=None,
            packet_text=_SOURCE_PACKET,
        )
        is False
    )


def test_planning_review_spawn_suppressed_falsifier_hybrid() -> None:
    assert (
        planning_review_spawn_suppressed(
            contract="light-bounded",
            review_surface="sidecar",
            dispatch_lane=_PATH_SIM_ADMIT_GATE,
            packet_text=_HYBRID_PACKET,
        )
        is False
    )


def test_planning_review_spawn_suppressed_belt_lane() -> None:
    belt_packet = """
<task_guidance>
Write deliverable to cortex://notes/system/specs/foo.md
STOP after A sidecar — ¬ implement.
</task_guidance>
<output_format>
fs(op="write", path="cortex://notes/system/specs/foo.md")
</output_format>
"""
    assert (
        planning_review_spawn_suppressed(
            contract="light-bounded",
            review_surface="unknown",
            dispatch_lane=_PATH_SIM_ADMIT_GATE,
            packet_text=belt_packet,
        )
        is True
    )


def test_resolve_dispatch_lane_from_thread_slug() -> None:
    assert (
        resolve_dispatch_lane_for_generate(
            dispatch_lane=None,
            source_ref=None,
            dispatch_thread_id="path-sim-my-slug",
        )
        == _PATH_SIM_ADMIT_GATE
    )


def test_stamp_lb_review_spawn_fields_persists_sidecar_suppress() -> None:
    review_surface, dispatch_lane, suppress = stamp_lb_review_spawn_fields(
        contract="light-bounded",
        early_packet_text=_SIDECAR_PACKET,
        dispatch_lane=None,
        source_ref=None,
        dispatch_thread_id="path-sim-my-slug",
        request_id="req-stamp",
    )
    assert review_surface == "sidecar"
    assert dispatch_lane == _PATH_SIM_ADMIT_GATE
    assert suppress is True


def test_admission_context_round_trip_review_spawn_fields() -> None:
    write_admission_context(
        execution_id="exec-roundtrip",
        auto_review_child=True,
        op="generate",
        role="cursor-sdk",
        resolved_model="cursor/claude-sonnet-5",
        parent_dispatch_thread_id="thread:parent",
        dispatch_thread_id="path-sim-my-slug",
        review_surface="sidecar",
        dispatch_lane=_PATH_SIM_ADMIT_GATE,
        suppress_review_spawn=True,
    )
    ctx = read_admission_context("exec-roundtrip")
    assert ctx is not None
    assert ctx.review_surface == "sidecar"
    assert ctx.dispatch_lane == _PATH_SIM_ADMIT_GATE
    assert ctx.suppress_review_spawn is True
    assert should_spawn_review_child(ctx) is False
