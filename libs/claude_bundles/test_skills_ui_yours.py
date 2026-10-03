"""Hermetic filters for the Customize Yours card list."""

from __future__ import annotations

from claude_bundles.skills_ui_yours import slugs_from_card_labels


def test_card_labels_drop_count_badge_and_keep_slugs() -> None:
    labels = ["55", "directive-authoring-standard", "Advisor Timing", "fs", "by you"]
    assert slugs_from_card_labels(labels) == {
        "directive-authoring-standard",
        "fs",
    }
