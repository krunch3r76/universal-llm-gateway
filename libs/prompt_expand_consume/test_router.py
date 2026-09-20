"""Unit tests for prompt-expand consume router precedence and parsers."""

from __future__ import annotations

import pytest
from implement_admission.prompt_expand_admit import already_expanded, should_expand
from prompt_expand_consume.router import (
    ConsumeBranch,
    build_activation_envelope,
    parse_fire_hint,
    parse_operator_verb,
    route_consume,
    stamp_expand_provenance,
)


@pytest.mark.offline
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("fire_hint: in_seat", "in_seat"),
        ("fire_hint: tab", "tab"),
        ("  fire_hint: BACKGROUND  ", "background"),
        ("---\nfire_hint: sdk\n---", "sdk"),
    ],
)
def test_parse_fire_hint(header: str, expected: str) -> None:
    assert parse_fire_hint(header) == expected


@pytest.mark.offline
def test_parse_fire_hint_ignores_body_after_40_lines() -> None:
    head = "\n".join(["# title"] * 40)
    body = "fire_hint: background"
    assert parse_fire_hint(head + "\n" + body) is None


@pytest.mark.offline
@pytest.mark.parametrize(
    ("commission", "expected"),
    [
        ("Please run this in the window when ready", "window"),
        ("Handle here in cursor after expand", "window"),
        ("Same tab execute only", "window"),
        ("Dispatch cursor-sdk while I'm away", "background"),
        ("sdk background is fine", "background"),
        (
            "Run in the window even though sdk background is mentioned later",
            "window",
        ),
    ],
)
def test_parse_operator_verb(commission: str, expected: str) -> None:
    assert parse_operator_verb(commission) == expected


@pytest.mark.offline
@pytest.mark.parametrize(
    ("task_prime", "commission", "attended", "durable", "branch", "reason_prefix"),
    [
        (
            "fire_hint: in_seat\n\nTASK'",
            "ignored",
            False,
            False,
            ConsumeBranch.IN_SEAT,
            "fire_hint:",
        ),
        (
            "fire_hint: background\n\nTASK'",
            "in the window",
            True,
            False,
            ConsumeBranch.SDK_BACKGROUND,
            "fire_hint:",
        ),
        (
            "fire_hint: conductor\n\nTASK'",
            "background please",
            True,
            True,
            ConsumeBranch.CONDUCTOR_RECOMMEND,
            "fire_hint:",
        ),
        (
            "---\npipeline: prompt-expand\n---\n\nTASK'",
            "expand then run in the window",
            True,
            False,
            ConsumeBranch.IN_SEAT,
            "operator_verb:window",
        ),
        (
            "---\npipeline: prompt-expand\n---\n\nTASK'",
            "ship sdk background",
            True,
            False,
            ConsumeBranch.SDK_BACKGROUND,
            "operator_verb:background",
        ),
        (
            "---\npipeline: prompt-expand\n---\n\nTASK'",
            "run in the window with sdk background mentioned",
            True,
            False,
            ConsumeBranch.IN_SEAT,
            "operator_verb:window",
        ),
        (
            "---\npipeline: prompt-expand\n---\n\nTASK'",
            "plain commission",
            False,
            False,
            ConsumeBranch.SDK_BACKGROUND,
            "default",
        ),
        (
            "---\npipeline: prompt-expand\n---\n\nTASK'",
            "plain commission",
            True,
            True,
            ConsumeBranch.CONDUCTOR_RECOMMEND,
            "durable_session_attended_fallback",
        ),
    ],
)
def test_route_consume_precedence_matrix(
    task_prime: str,
    commission: str,
    attended: bool,
    durable: bool,
    branch: ConsumeBranch,
    reason_prefix: str,
) -> None:
    decision = route_consume(
        task_prime=task_prime,
        original_commission=commission,
        attended=attended,
        durable_session=durable,
        summoning_thread_id="11806",
    )
    assert decision.branch is branch
    assert decision.reason.startswith(reason_prefix)
    if branch is ConsumeBranch.IN_SEAT:
        assert decision.activation_header is not None
        assert decision.activation_header["X-ULG-Summoning-Thread"] == "11806"


@pytest.mark.offline
def test_build_activation_envelope_omits_empty_optional_fields() -> None:
    envelope = build_activation_envelope("TASK'", summoning_thread_id=None, transcript_id=None)
    assert envelope["X-ULG-Consume-Branch"] == "in_seat"
    assert "X-ULG-Summoning-Thread" not in envelope
    assert "X-ULG-Transcript-Id" not in envelope


@pytest.mark.offline
def test_stamp_expand_provenance_idempotent() -> None:
    stamped = stamp_expand_provenance("TASK body", "exp-1")
    assert already_expanded(stamped)
    assert stamp_expand_provenance(stamped, "exp-2") == stamped


@pytest.mark.offline
def test_already_expanded_skips_second_admit() -> None:
    task = stamp_expand_provenance("# packet\n\nDo work", "exp-3")
    decision = should_expand(
        prompt=task,
        contract="implement",
        caller_agent="web-anthropic",
        parent_thread="10479",
    )
    assert decision.admit is False
    assert decision.skip_reason == "already_expanded"
