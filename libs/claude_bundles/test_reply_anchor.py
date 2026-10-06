"""Unit tests for ReplyAnchor and prompt marker derivation."""

from __future__ import annotations

import pytest

from claude_bundles.reply_anchor import (
    ReplyAnchor,
    anchor_marker,
    typed_prompt_body,
    unique_anchor,
)

pytestmark = pytest.mark.offline


def test_marker_is_substring_of_typed_draft_no_skills() -> None:
    prompt = "Read the file and reply with its first line."
    marker = anchor_marker(prompt)
    typed = typed_prompt_body(prompt)
    assert marker in typed
    assert "/" not in marker or marker.startswith("#")


def test_marker_excludes_slash_manifest_on_marked_sealed_prompt() -> None:
    from claude_bundles.cowork_skill_delivery import prepend_cdp_dispatch_skills

    prompt, _used, _bodies = prepend_cdp_dispatch_skills(
        "Question: what is the bind?\nAnswer in one paragraph.",
        ["reasoning-posture"],
    )
    typed = typed_prompt_body(prompt)
    assert "/reasoning-posture" not in typed.split("<skills_inline>", 1)[0].strip()
    marker = anchor_marker(prompt)
    assert marker in typed
    assert "Question: what is the bind?" in marker


def test_marker_prefers_unique_token_on_marked_path() -> None:
    prompt = (
        "#2-unique: harvest-canary-abc123\n"
        "Use the sealed skill and summarize.\n"
    )
    assert anchor_marker(prompt) == "#2-unique: harvest-canary-abc123"


def test_symbol_only_typed_body_raises() -> None:
    prompt = "---\n***\n..."
    with pytest.raises(ValueError, match="no letters or digits"):
        anchor_marker(prompt)


def test_unique_anchor_requires_token() -> None:
    with pytest.raises(ValueError, match="lacks #N-unique"):
        unique_anchor("plain prompt without token")
    anchor = unique_anchor("#1-unique: stop-ack-deadbeef-cafe\nSTOP-ACK\n")
    assert anchor.prior_matches == 0
    assert anchor.marker == "#1-unique: stop-ack-deadbeef-cafe"


def test_reply_anchor_after_pre_state() -> None:
    base = ReplyAnchor(marker="Question: bind", prior_matches=0)
    updated = base.after({"anchor_matches": 2})
    assert updated.prior_matches == 2
    assert updated.marker == base.marker


def test_js_args_shape() -> None:
    args = ReplyAnchor(marker="hop", prior_matches=1).js_args()
    assert args["anchorMarker"] == "hop"
    assert args["priorMatches"] == 1
    assert len(args["userSelectors"]) == 3
