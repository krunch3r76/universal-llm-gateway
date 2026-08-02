"""Tests for cursor-sdk closeout bus subject leg provenance."""

from __future__ import annotations

from services.git_integration_worker.cursor_sdk_closeout_subject import (
    build_sdk_closeout_subject,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

_BEFORE_SUBJECT = "cursor-sdk dispatch {dispatch_id}"


def test_build_sdk_closeout_subject_implement_leg() -> None:
    req = CursorDispatchRequest(
        thread_id="440",
        model="cursor/composer-2.5",
        dispatch_id="auto-440",
        execution_id="exec-440",
        message="implement packet",
        handoff_contract="implement",
        admitted_via="cursor-auto",
        caller_agent="cursor-auto",
    )
    subject = build_sdk_closeout_subject(req, contract="implement")
    assert subject == (
        "cursor-sdk CLOSEOUT auto-440 contract=implement "
        "admitted_via=cursor-auto caller=cursor-auto"
    )
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
        admitted_via="cursor-auto",
    )
    read_only_subject = build_sdk_closeout_subject(req, contract="consult")
    implement_subject = build_sdk_closeout_subject(implement_req, contract="implement")
    assert read_only_subject == (
        "cursor-sdk CLOSEOUT auto-448 contract=consult read_only admitted_via=stargate"
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
        admitted_via="cursor-auto",
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
        admitted_via="cursor-auto",
    )
    assert "nest=" not in build_sdk_closeout_subject(without_nest, contract="implement")
