"""F4 — Pydantic model_validator coverage for GrokbuildDispatchRequest.

Locks in the rejection contract added after the master @ cab52fa7 session
review: when ``mcp=False`` (api path), grok-CLI-only fields MUST raise
``ValidationError`` at admission rather than silently dropping at the
worker. See ``services.grokbuild_worker.models.async_dispatch.
GrokbuildDispatchRequest._validate_mcp_compatibility``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.grokbuild_worker.models.async_dispatch import GrokbuildDispatchRequest


def test_mcp_true_with_edit_mode_admitted() -> None:
    """CLI path admits edit mode plus all grok-CLI flags — unchanged."""
    req = GrokbuildDispatchRequest(
        cwd="/tmp/x",
        prompt="p",
        mode="edit",
        mcp=True,
        effort="high",
        reasoning_effort="high",
        no_subagents=True,
        max_turns=10,
    )
    assert req.mode == "edit"
    assert req.mcp is True


def test_mcp_false_with_all_defaults_admitted() -> None:
    """Api path with default values has no incompatibilities."""
    req = GrokbuildDispatchRequest(
        cwd="/tmp/x",
        prompt="p",
        mcp=False,
    )
    assert req.mcp is False
    assert req.mode == "read_only"


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("mode", "edit", "mode='edit'"),
        ("continue_recent", True, "continue_recent=True"),
        ("reasoning_effort", "high", "reasoning_effort"),
        ("effort", "max", "effort"),
        ("check", True, "check"),
        ("no_subagents", True, "no_subagents=True"),
        ("disable_web_search", True, "disable_web_search=True"),
        ("max_turns", 5, "max_turns"),
        ("best_of_n", 4, "best_of_n"),
        ("resume_strict", True, "resume_strict=True"),
    ],
)
def test_mcp_false_rejects_each_incompatible_field(
    field: str, value: object, fragment: str
) -> None:
    """Each grok-CLI-only field is independently rejected on the api path.

    Locks in that silently-dropped intent is now a hard 422 at admission.
    """
    kwargs: dict[str, object] = {
        "cwd": "/tmp/x",
        "prompt": "p",
        "mcp": False,
        field: value,
    }
    # resume_strict=True alone also fails the existing `resume_strict requires
    # session_id` validator if it ran first; this test only asserts that
    # _validate_mcp_compatibility surfaces resume_strict in its message.
    if field == "resume_strict":
        kwargs["session_id"] = "sid"
    with pytest.raises(ValidationError) as exc_info:
        GrokbuildDispatchRequest(**kwargs)  # type: ignore[arg-type]
    msg = str(exc_info.value)
    assert "mcp=False" in msg
    assert fragment in msg


def test_mcp_false_aggregates_multiple_incompatibles_into_single_message() -> None:
    """When multiple fields conflict the validator reports all of them at once."""
    with pytest.raises(ValidationError) as exc_info:
        GrokbuildDispatchRequest(
            cwd="/tmp/x",
            prompt="p",
            mcp=False,
            mode="edit",
            effort="high",
            max_turns=3,
        )
    msg = str(exc_info.value)
    assert "mode='edit'" in msg
    assert "effort" in msg
    assert "max_turns" in msg
