"""Hermetic tests for compose-setup failure classification (toggle vs approval vs auth)."""

from __future__ import annotations

import json

from claude_bundles.chat_session_hygiene import (
    _compose_setup_error,
    classify_compose_setup_failure,
    compose_auth_failure_hint,
)


def test_auth_hint_on_logout_and_login() -> None:
    assert compose_auth_failure_hint("https://claude.ai/logout") == (
        "new_compose_unauthenticated"
    )
    assert compose_auth_failure_hint("https://claude.ai/login?from=logout") == (
        "new_compose_unauthenticated"
    )
    assert compose_auth_failure_hint("https://claude.ai/new") is None


def test_b7ea437d_fingerprint_is_approval_not_toggle() -> None:
    """Live 20:08:28Z stall: Cowork attest ok, approval stuck on Manually approve."""
    result = {
        "ok": False,
        "step": "cowork_auto",
        "mode": {
            "ok": True,
            "step": "selected_cowork",
            "before": {
                "title": "New chat - Claude",
                "mode": "chat",
                "approval": None,
            },
            "after": {
                "title": "New task - Claude",
                "mode": "cowork",
                "approval": {"aria": "Manually approve", "text": "Manual"},
            },
            "via": "playwright",
            "attest": {"ok": True, "step": "attested_cowork"},
        },
        "approval": {
            "ok": False,
            "step": "select_auto_no_attest",
            "after": {
                "mode": "cowork",
                "approval": {"aria": "Manually approve"},
            },
        },
    }
    classified = classify_compose_setup_failure(
        result, url="https://claude.ai/new", on_new=True
    )
    assert classified["failure_class"] == "approval"
    assert classified["hint"] == "new_compose_approval_failed"
    assert classified["stuck_manual"] is True
    assert classified["mode_ok"] is True

    err = _compose_setup_error(
        step="ensure_cowork_auto",
        url="https://claude.ai/new",
        result=result,
        on_new=True,
    )
    payload = json.loads(str(err).split("failed: ", 1)[1])
    assert payload["failure_class"] == "approval"
    assert payload["hint"] == "new_compose_approval_failed"
    assert payload["stuck_manual"] is True
    assert payload["mode"]["attest"]["ok"] is True
    assert payload["approval"]["ok"] is False
    # Must not mislabel a successful toggle as the failure.
    assert payload["hint"] != "new_compose_toggle_failed"


def test_toggle_failure_keeps_toggle_hint() -> None:
    result = {
        "ok": False,
        "step": "cowork",
        "mode": {
            "ok": False,
            "step": "chip_missing",
            "before": {"mode": "chat", "approval": None},
        },
    }
    classified = classify_compose_setup_failure(
        result, url="https://claude.ai/new", on_new=True
    )
    assert classified["failure_class"] == "toggle"
    assert classified["hint"] == "new_compose_toggle_failed"


def test_logout_url_classifies_unauthenticated_over_nested_blocks() -> None:
    result = {
        "ok": False,
        "step": "cowork",
        "mode": {"ok": False, "step": "chip_missing"},
    }
    classified = classify_compose_setup_failure(
        result, url="https://claude.ai/logout", on_new=True
    )
    assert classified["failure_class"] == "unauthenticated"
    assert classified["hint"] == "new_compose_unauthenticated"


def test_chip_missing_payload_includes_fingerprint_and_candidates() -> None:
    """Toggle failure must carry verbatim fingerprint + chip census (friction 25052)."""
    fp = {
        "title": "New chat - Claude",
        "mode": "chat",
        "approval": None,
        "url": "https://claude.ai/new",
    }
    candidates = [{"text": "Cowork", "tag": "SPAN", "role": "radio", "w": 67.8, "h": 26}]
    result = {
        "ok": False,
        "step": "cowork",
        "mode": {
            "ok": False,
            "step": "chip_missing",
            "wanted": "cowork",
            "before": fp,
            "compose_mode_fingerprint": fp,
            "candidates": candidates,
        },
    }
    err = _compose_setup_error(
        step="ensure_cowork_auto",
        url="https://claude.ai/new",
        result=result,
        on_new=True,
    )
    payload = json.loads(str(err).split("failed: ", 1)[1])
    assert payload["inner_step"] == "chip_missing"
    assert payload["compose_mode_fingerprint"] == fp
    assert payload["candidates"] == candidates
    assert payload["failure_class"] == "toggle"
