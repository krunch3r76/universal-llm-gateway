"""Stargate chat completion accepts writer intake without non-empty messages."""

from __future__ import annotations

import pytest

from src.schemas.chat_completion import ChatCompletionRequest


def test_intake_only_empty_messages_list() -> None:
    ChatCompletionRequest(
        model="writer-specialist-v1",
        messages=[],
        pipeline_options={"intake": {"mode": "standard"}},
    )


def test_intake_only_no_messages_key_json_string() -> None:
    ChatCompletionRequest(
        model="writer-specialist-v1",
        pipeline_options={"intake": '{"mode": "standard"}'},
    )


def test_empty_intake_dict_without_messages_raises() -> None:
    with pytest.raises(ValueError):
        ChatCompletionRequest(
            model="writer-specialist-v1",
            pipeline_options={"intake": {}},
        )


def test_no_pipeline_options_no_messages_raises() -> None:
    with pytest.raises(ValueError):
        ChatCompletionRequest(model="writer-specialist-v1")


def test_empty_content_message_with_intake_ok() -> None:
    ChatCompletionRequest(
        model="writer-specialist-v1",
        messages=[{"role": "user", "content": ""}],
        pipeline_options={"intake": {"mode": "standard"}},
    )
