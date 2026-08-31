"""Regression tests for guardrail C — the MCP-layer half of the 1140->1142
silent-fork incident fix.

Covers the post-path reconciliation and rejection helpers:
  - `from` -> `from_agent` alias reconciliation (REST route parity)
  - actionable rejection envelopes for continuation-shaped keys on post
  - surfacing the REST route's structured guard envelope (guardrail A)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools._agent_bus_post_guard import (  # noqa: E402
    reconcile_post_arguments,
    structured_route_guard,
)


def _legit_post() -> dict[str, object]:
    return {
        "slug": "deploy-failure-report",
        "from_agent": "claude-web",
        "to": "cursor",
        "subject": "s",
        "body": "b",
    }


def test_from_alias_reconciled_to_from_agent() -> None:
    args = {
        "slug": "x",
        "from": "claude-web",
        "to": "cursor",
        "subject": "s",
        "body": "b",
    }
    normalized, misuse = reconcile_post_arguments(args)
    assert misuse is None
    assert "from" not in normalized
    assert normalized["from_agent"] == "claude-web"


def test_explicit_from_agent_wins_over_alias() -> None:
    args = {"from": "alias-seat", "from_agent": "explicit-seat", "slug": "x"}
    normalized, misuse = reconcile_post_arguments(args)
    assert misuse is None
    assert normalized["from_agent"] == "explicit-seat"
    assert "from" not in normalized


def test_thread_key_rejected_with_actionable_envelope() -> None:
    args = _legit_post() | {"thread": "1140"}
    _, misuse = reconcile_post_arguments(args)
    assert misuse is not None
    assert misuse["reason"] == "thread_not_valid_on_post"
    assert misuse["suggestion"] == "use_send_to_continue"
    assert "send(" in misuse["error"]


def test_after_turn_key_rejected_with_actionable_envelope() -> None:
    args = _legit_post() | {"after_turn": 4}
    _, misuse = reconcile_post_arguments(args)
    assert misuse is not None
    assert misuse["reason"] == "after_turn_not_valid_on_post"
    assert misuse["after_turn"] == 4
    assert "send(" in misuse["error"]


def test_after_turn_zero_skip_sentinel_stripped_on_post() -> None:
    args = _legit_post() | {"after_turn": 0}
    normalized, misuse = reconcile_post_arguments(args)
    assert misuse is None
    assert "after_turn" not in normalized


def test_legit_post_passes_clean() -> None:
    normalized, misuse = reconcile_post_arguments(_legit_post())
    assert misuse is None
    assert normalized == _legit_post()


def test_structured_route_guard_surfaces_numeric_slug_envelope() -> None:
    # Mirrors guardrail A's 400 detail flowing back through the relay.
    relay_result = {
        "error": "HTTP 400",
        "status_code": 400,
        "detail": {
            "reason": "slug_looks_like_thread_id",
            "slug": "1140",
            "message": "slug '1140' is all digits and looks like a thread ID. ...",
            "suggestion": "use_reply_to_continue",
        },
    }
    envelope = structured_route_guard(relay_result)
    assert envelope is not None
    assert envelope["reason"] == "slug_looks_like_thread_id"
    assert envelope["slug"] == "1140"
    assert envelope["suggestion"] == "use_reply_to_continue"
    assert envelope["error"].startswith("slug '1140' is all digits")


def test_structured_route_guard_passes_through_unstructured_error() -> None:
    assert structured_route_guard({"error": "Connection failed to agent-bus"}) is None
    assert (
        structured_route_guard({"error": "HTTP 500", "detail": "plain string"}) is None
    )
