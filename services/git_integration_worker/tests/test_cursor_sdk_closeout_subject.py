"""Tests for cursor-sdk closeout bus subject leg provenance."""

from __future__ import annotations

from services.git_integration_worker.cursor_sdk_closeout_subject import (
    build_sdk_closeout_subject,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

_BEFORE_SUBJECT = "cursor-sdk dispatch {dispatch_id}"


def test_build_sdk_closeout_subject_investigate_handoff_leg() -> None:
    req = CursorDispatchRequest(
        thread_id="472",
        model="cursor/composer-2.5",
        dispatch_id="auto-472",
        execution_id="exec-472",
        message="TYPE: DIRECTIVE\ncontract: investigate\nhandoff=none\n",
        handoff_contract="none",
        admitted_via="stargate",
        caller_agent="stargate",
    )
    subject = build_sdk_closeout_subject(req, contract="none")
    assert subject == (
        "CLOSEOUT|cursor-sdk CLOSEOUT auto-472 exec=exec-472 contract=investigate "
        "handoff=none admitted_via=stargate caller=stargate"
    )


def test_build_sdk_closeout_subject_implement_leg() -> None:
    req = CursorDispatchRequest(
        thread_id="440",
        model="cursor/composer-2.5",
        dispatch_id="auto-440",
        execution_id="exec-440",
        message="implement packet",
        handoff_contract="implement",
        admitted_via="stargate",
        caller_agent="stargate",
    )
    subject = build_sdk_closeout_subject(req, contract="implement")
    assert subject == (
        "CLOSEOUT|cursor-sdk CLOSEOUT auto-440 exec=exec-440 contract=implement "
        "admitted_via=stargate caller=stargate"
    )
    assert subject.startswith("CLOSEOUT|")
    assert "read_only" not in subject
    assert subject != _BEFORE_SUBJECT.format(dispatch_id="auto-440")


def test_build_sdk_closeout_subject_read_only_leg() -> None:
    req = CursorDispatchRequest(
        thread_id="448",
        model="cursor/composer-2.5",
        dispatch_id="auto-448",
        execution_id="exec-448",
        message="reflex read",
        handoff_contract="consult",
        read_only=True,
        admitted_via="stargate",
    )
    implement_req = CursorDispatchRequest(
        thread_id="440",
        model="cursor/composer-2.5",
        dispatch_id="auto-440",
        execution_id="exec-440",
        message="implement packet",
        handoff_contract="implement",
        admitted_via="stargate",
    )
    read_only_subject = build_sdk_closeout_subject(req, contract="consult")
    implement_subject = build_sdk_closeout_subject(implement_req, contract="implement")
    assert read_only_subject == (
        "CLOSEOUT|cursor-sdk CLOSEOUT auto-448 exec=exec-448 contract=consult "
        "read_only admitted_via=stargate"
    )
    assert read_only_subject != implement_subject
    assert "read_only" in read_only_subject
    assert "read_only" not in implement_subject


def test_build_sdk_closeout_subject_nested_only_when_present() -> None:
    req = CursorDispatchRequest(
        thread_id="6692",
        model="cursor/composer-2.5",
        dispatch_id="auto-child",
        execution_id="exec-child",
        message="nested",
        handoff_contract="implement",
        admitted_via="stargate",
        nest_under="auto-parent",
    )
    subject = build_sdk_closeout_subject(req, contract="implement")
    assert "nest=auto-parent" in subject

    without_nest = CursorDispatchRequest(
        thread_id="6692",
        model="cursor/composer-2.5",
        dispatch_id="auto-child",
        execution_id="exec-child",
        message="nested",
        handoff_contract="implement",
        admitted_via="stargate",
    )
    assert "nest=" not in build_sdk_closeout_subject(without_nest, contract="implement")


def test_build_sdk_closeout_subject_specimen_22723_turn6() -> None:
    """B7: modelled on thread 22723 turn 6 plus exec= from turn 5 admit."""
    req = CursorDispatchRequest(
        thread_id="22723",
        model="cursor/composer-2.5",
        dispatch_id="fca8314b-4448-4f41-a0a2-2ad9976162d9",
        execution_id="90063c9a-e374-44e7-8f56-a353670fd7cd",
        message="contract: mechanical\ngenerate",
        caller_agent="web-anthropic",
    )
    subject = build_sdk_closeout_subject(req, contract="mechanical")
    assert subject == (
        "CLOSEOUT|cursor-sdk CLOSEOUT fca8314b-4448-4f41-a0a2-2ad9976162d9 "
        "exec=90063c9a-e374-44e7-8f56-a353670fd7cd contract=mechanical "
        "caller=web-anthropic"
    )
