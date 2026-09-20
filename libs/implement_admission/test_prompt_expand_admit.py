"""Caller-door admit predicate for prompt-expand (10479 night hops)."""

from __future__ import annotations

import pytest

from implement_admission.prompt_expand_admit import (
    PIPELINE_ID,
    expand_options,
    should_expand,
)

_TASK = "# Next-Seat Dispatch Packet\n\nImplement the catalog skip."
_WAKE = (
    "WAKE — liaison, house agent-bus:10479 claude-ai-navigator-seat\n"
    "addresses: agent-bus:10479\n"
)


@pytest.mark.offline
def test_10479_implement_skipped() -> None:
    decision = should_expand(
        prompt=_TASK,
        contract="implement",
        caller_agent="web-anthropic",
        parent_thread="10479",
    )
    assert decision.admit is False
    assert decision.root == "10479"
    assert decision.skip_reason == "implement"


@pytest.mark.offline
def test_wake_doorbell_skipped() -> None:
    decision = should_expand(
        prompt=_WAKE,
        contract="none",
        caller_agent="liaison-ticker",
        dispatch_thread_id="10479",
    )
    assert decision.admit is False
    assert decision.skip_reason == "liaison_ticker"


@pytest.mark.offline
def test_wake_text_skipped_even_from_navigator() -> None:
    decision = should_expand(
        prompt=_WAKE,
        contract="none",
        caller_agent="web-anthropic",
        dispatch_thread_id="10479",
    )
    assert decision.admit is False
    assert decision.skip_reason == "wake_doorbell"


@pytest.mark.offline
def test_foreign_root_skipped() -> None:
    decision = should_expand(
        prompt=_TASK,
        contract="implement",
        caller_agent="cursor",
        parent_thread="99999",
    )
    assert decision.admit is False
    assert decision.skip_reason == "root_not_enrolled"


@pytest.mark.offline
def test_already_expanded_skipped() -> None:
    primed = f"---\n{PIPELINE_ID}: kept\npipeline: prompt-expand\n---\n" + _TASK
    decision = should_expand(
        prompt=primed,
        contract="implement",
        caller_agent="cursor",
        parent_thread="10479",
    )
    assert decision.admit is False
    assert decision.skip_reason == "already_expanded"


@pytest.mark.offline
def test_11738_enrolled_by_default() -> None:
    decision = should_expand(
        prompt=_TASK,
        contract="sketch",
        caller_agent="cursor",
        dispatch_thread_id="11738",
    )
    assert decision.admit is True
    assert decision.root == "11738"


@pytest.mark.offline
def test_11834_enrolled_by_default() -> None:
    decision = should_expand(
        prompt=_TASK,
        contract="sketch",
        caller_agent="cursor",
        dispatch_thread_id="11834",
    )
    assert decision.admit is True
    assert decision.root == "11834"


@pytest.mark.offline
def test_11667_enrolled_by_default() -> None:
    decision = should_expand(
        prompt=_TASK,
        contract="sketch",
        caller_agent="cursor",
        dispatch_thread_id="11667",
    )
    assert decision.admit is True
    assert decision.root == "11667"


@pytest.mark.offline
def test_enrolled_conductor_skipped() -> None:
    decision = should_expand(
        prompt=_TASK,
        contract="conductor",
        caller_agent="cursor",
        dispatch_thread_id="10479",
    )
    assert decision.admit is False
    assert decision.root == "10479"
    assert decision.skip_reason == "implement"


@pytest.mark.offline
def test_enrolled_none_still_admits() -> None:
    decision = should_expand(
        prompt=_TASK,
        contract="none",
        caller_agent="cursor",
        dispatch_thread_id="10479",
    )
    assert decision.admit is True
    assert decision.root == "10479"


@pytest.mark.offline
def test_enrolled_sketch_admits() -> None:
    decision = should_expand(
        prompt=_TASK,
        contract="sketch",
        caller_agent="cursor",
        dispatch_thread_id="10479",
    )
    assert decision.admit is True
    assert decision.root == "10479"
    assert decision.skip_reason is None


@pytest.mark.offline
def test_unenrolled_sketch_still_skipped() -> None:
    decision = should_expand(
        prompt=_TASK,
        contract="sketch",
        caller_agent="cursor",
        dispatch_thread_id="99999",
    )
    assert decision.admit is False
    assert decision.skip_reason == "root_not_enrolled"


@pytest.mark.offline
def test_wrap_still_mechanical() -> None:
    decision = should_expand(
        prompt=_TASK,
        contract="wrap",
        caller_agent="cursor",
        parent_thread="10479",
    )
    assert decision.admit is False
    assert decision.skip_reason == "mechanical"


@pytest.mark.offline
def test_sketch_maps_to_consult_g1() -> None:
    opts = expand_options(contract="sketch", seat="cursor-sdk")
    assert opts["contract"] == "consult"
    assert opts["stage"] == "g1"
    assert opts["target"] == "cursor"


@pytest.mark.offline
def test_conductor_maps_to_implement_g5() -> None:
    opts = expand_options(contract="conductor", seat="cursor-sdk")
    assert opts["contract"] == "implement"
    assert opts["stage"] == "g5"
    assert opts["target"] == "cursor"
    assert opts["rag_fail"] == "stamp"
    assert opts["delivery"] == "prompt"


@pytest.mark.offline
def test_cdp_seat_maps_target() -> None:
    opts = expand_options(contract="consult", model="cdp/opus-5")
    assert opts["target"] == "cdp"
    assert opts["stage"] == "g1"
