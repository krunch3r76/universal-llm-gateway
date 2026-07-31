"""Contract tests for ?pseudostream=true validation."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from systems.proxy.core.nonstreaming.pseudostream_policy import (
    is_local_pseudostream_eligible,
    validate_pseudostream_request,
)


@pytest.mark.parametrize(
    "model,eligible",
    [
        ("hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid", True),
        ("qwen3-32b-32768", True),
        ("openrouter/nousresearch/hermes-3-llama-3.1-70b", False),
        ("anthropic/claude-opus-4-8", False),
        ("gpt-5.4-mini", False),
        ("cursor/composer-2", False),
    ],
)
def test_local_eligibility(model: str, eligible: bool) -> None:
    assert is_local_pseudostream_eligible(model) is eligible


def test_validate_noop_when_false() -> None:
    validate_pseudostream_request(
        pseudostream=False,
        client_stream=True,
        is_pipeline=True,
        model="openrouter/x",
    )


def test_validate_rejects_stream_conflict() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_pseudostream_request(
            pseudostream=True,
            client_stream=True,
            is_pipeline=False,
            model="hermes-3-local",
        )
    assert exc.value.status_code == 400


def test_validate_rejects_pipeline() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_pseudostream_request(
            pseudostream=True,
            client_stream=False,
            is_pipeline=True,
            model="hermes-3-local",
        )
    assert exc.value.status_code == 400


def test_validate_rejects_cloud() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_pseudostream_request(
            pseudostream=True,
            client_stream=False,
            is_pipeline=False,
            model="openrouter/writer/palmyra-x5",
        )
    assert exc.value.status_code == 400


def test_validate_accepts_local() -> None:
    validate_pseudostream_request(
        pseudostream=True,
        client_stream=False,
        is_pipeline=False,
        model="hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid",
    )
