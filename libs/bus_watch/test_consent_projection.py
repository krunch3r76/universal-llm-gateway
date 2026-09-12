"""Tests for consent → gates projection (F3, C3)."""

from __future__ import annotations

import pytest

from bus_watch.consent_projection import (
    DEFAULT_GATE_CLASSES,
    echo,
    is_gated,
    project_gates,
)

pytestmark = pytest.mark.offline

NOW = "2026-09-11T12:00:00Z"


def test_defaults_present() -> None:
    block = project_gates([], now=NOW, let_drive_ttl_days=30)
    gate_classes = {g["class"] for g in block["gates"]}
    assert gate_classes == set(DEFAULT_GATE_CLASSES)
    for gate in block["gates"]:
        assert gate["source"] == "F3-default"
        assert gate["scope"] is None


def test_let_drive_default_expiry_and_expired_dropped() -> None:
    block = project_gates(
        [
            {
                "id": "a:1",
                "class": "let-drive",
                "gate_class": "outbound_correspondence",
                "scope": "landlord",
            }
        ],
        now=NOW,
        let_drive_ttl_days=30,
    )
    assert len(block["lifts"]) == 1
    assert block["lifts"][0]["expiry"] == "2026-10-11T12:00:00Z"

    expired = project_gates(
        [
            {
                "id": "a:2",
                "class": "let-drive",
                "gate_class": "money",
                "scope": "bank",
                "expiry": "2026-09-01T00:00:00Z",
            }
        ],
        now=NOW,
        let_drive_ttl_days=30,
    )
    assert expired["lifts"] == []


def test_is_gated_scope_match_and_endeavor_wide_lift() -> None:
    block = project_gates([], now=NOW, let_drive_ttl_days=30)
    assert is_gated("outbound_correspondence", "landlord", block) is True

    scoped = project_gates(
        [
            {
                "id": "a:3",
                "class": "let-drive",
                "gate_class": "outbound_correspondence",
                "scope": "landlord",
                "expiry": "2026-12-01T00:00:00Z",
            }
        ],
        now=NOW,
        let_drive_ttl_days=30,
    )
    assert is_gated("outbound_correspondence", "landlord", scoped) is False
    assert is_gated("outbound_correspondence", "other", scoped) is True

    wide = project_gates(
        [
            {
                "id": "a:4",
                "class": "let-drive",
                "gate_class": "money",
                "scope": None,
                "expiry": "2026-12-01T00:00:00Z",
            }
        ],
        now=NOW,
        let_drive_ttl_days=30,
    )
    assert is_gated("money", "any-scope", wide) is False


def test_echo_strings_byte_exact() -> None:
    assert (
        echo("steer", target="health referral")
        == "Switching to health referral; the old next step is parked."
    )
    assert (
        echo("gate", target="landlord")
        == "Understood; nothing goes to landlord without asking first."
    )
    assert (
        echo("let-drive", scope="landlord", expiry="2026-10-01")
        == "I'll write to landlord myself until 2026-10-01; you'll see each after it goes."
    )


def test_echo_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown consent kind"):
        echo("bogus")
