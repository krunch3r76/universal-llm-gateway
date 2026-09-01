"""Falsifier: the cursor-sdk dispatch HOME must not carry the human operator register.

`operator-posture` teaches a seat to address a human reader (Been→Are→Going
orientation, "What I need from you"). No human reads a headless dispatch, so the
per-dispatch HOME prunes it and grafts `interagent-posture` from the seat overlay.
These tests assert the swap actually took effect — a silent no-op would leave the
seat addressing its dispatching lead, a model, as a person.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.git_integration_worker.cursor_seat_overlay import (
    ECOSYSTEM_PLUGIN_RELPATH,
    LEAD_ONLY_PLUGIN_PATHS,
    PRUNE_ONLY_PLUGIN_PATHS,
    PRUNED_PLUGIN_PATHS,
    SeatOverlayConfigError,
    apply_cursor_sdk_seat_overlay,
    seat_overlay_root,
)


def _fake_dispatch_cursor_dir(tmp_path: Path) -> Path:
    """A dispatch HOME `.cursor` seeded like a plugin copy of the operator's tree."""
    plugin_root = tmp_path / ".cursor" / ECOSYSTEM_PLUGIN_RELPATH
    (plugin_root / "rules").mkdir(parents=True)
    (plugin_root / "skills" / "operator-posture").mkdir(parents=True)
    (plugin_root / "rules" / "operator-posture_ulg.mdc").write_text(
        "What I need from you\n", encoding="utf-8"
    )
    (plugin_root / "skills" / "operator-posture" / "SKILL.md").write_text(
        "human register\n", encoding="utf-8"
    )
    for name, body in (
        ("cdp-operator-proxy_ulg.mdc", "Kaywan explicitly declares\n"),
        ("restart-drain-discipline_ulg.mdc", "same-window force classifier\n"),
        ("skill-surface_ulg.mdc", "claude.ai Customize upload\n"),
    ):
        (plugin_root / "rules" / name).write_text(body, encoding="utf-8")
    for relpath in LEAD_ONLY_PLUGIN_PATHS:
        (plugin_root / relpath).write_text("lead-only doctrine\n", encoding="utf-8")
    (plugin_root / "rules" / "presence-discipline_ulg.mdc").write_text(
        "unrelated\n", encoding="utf-8"
    )
    return tmp_path / ".cursor"


def _fake_overlay(tmp_path: Path) -> Path:
    root = tmp_path / "overlay"
    (root / "rules").mkdir(parents=True)
    (root / "skills" / "interagent-posture").mkdir(parents=True)
    (root / "rules" / "interagent-posture_ulg.mdc").write_text(
        "audience = agent_seat\n", encoding="utf-8"
    )
    (root / "skills" / "interagent-posture" / "SKILL.md").write_text(
        "closeout register\n", encoding="utf-8"
    )
    (root / "rules" / "restart-drain-discipline_ulg.mdc").write_text(
        "landed is not live\n", encoding="utf-8"
    )
    (root / "rules" / "skill-surface_ulg.mdc").write_text(
        "install-ecosystem-plugin.sh in this dispatch\n", encoding="utf-8"
    )
    return root


def test_human_register_pruned_and_interagent_grafted(tmp_path: Path) -> None:
    cursor_dir = _fake_dispatch_cursor_dir(tmp_path)
    pruned, grafted = apply_cursor_sdk_seat_overlay(
        cursor_dir, overlay_root=_fake_overlay(tmp_path)
    )

    plugin_root = cursor_dir / ECOSYSTEM_PLUGIN_RELPATH
    for relpath in PRUNE_ONLY_PLUGIN_PATHS:
        assert not (plugin_root / relpath).exists()
    assert set(pruned) == set(PRUNED_PLUGIN_PATHS) | set(LEAD_ONLY_PLUGIN_PATHS)

    assert (plugin_root / "rules" / "interagent-posture_ulg.mdc").is_file()
    assert (plugin_root / "skills" / "interagent-posture" / "SKILL.md").is_file()
    assert Path("rules") / "interagent-posture_ulg.mdc" in grafted

    # IDE human-framing rules replaced with SDK seat variants (or absent when prune-only).
    assert not (plugin_root / "rules" / "cdp-operator-proxy_ulg.mdc").exists()

    # Mixed rules: lead doctrine gone, the executor-actionable slice grafted back.
    drain = (plugin_root / "rules" / "restart-drain-discipline_ulg.mdc").read_text(
        encoding="utf-8"
    )
    assert "landed is not live" in drain
    assert "same-window force classifier" not in drain
    surface = (plugin_root / "rules" / "skill-surface_ulg.mdc").read_text(
        encoding="utf-8"
    )
    assert "install-ecosystem-plugin.sh" in surface
    assert "Customize upload" not in surface

    # Lead-only doctrine costs a re-send per agent step and governs nothing here.
    for relpath in LEAD_ONLY_PLUGIN_PATHS:
        assert not (plugin_root / relpath).exists(), relpath

    # Unrelated plugin rules keep IDE parity.
    assert (plugin_root / "rules" / "presence-discipline_ulg.mdc").is_file()


def test_lead_only_absence_is_tolerated(tmp_path: Path) -> None:
    """A lead-only rule missing from the operator tree must not fail the overlay."""
    cursor_dir = _fake_dispatch_cursor_dir(tmp_path)
    plugin_root = cursor_dir / ECOSYSTEM_PLUGIN_RELPATH
    (plugin_root / LEAD_ONLY_PLUGIN_PATHS[0]).unlink()

    pruned, _ = apply_cursor_sdk_seat_overlay(
        cursor_dir, overlay_root=_fake_overlay(tmp_path)
    )

    assert LEAD_ONLY_PLUGIN_PATHS[0] not in pruned
    assert set(PRUNED_PLUGIN_PATHS) <= set(pruned)


def test_idempotent_when_already_applied(tmp_path: Path) -> None:
    cursor_dir = _fake_dispatch_cursor_dir(tmp_path)
    overlay = _fake_overlay(tmp_path)
    apply_cursor_sdk_seat_overlay(cursor_dir, overlay_root=overlay)
    pruned, grafted = apply_cursor_sdk_seat_overlay(cursor_dir, overlay_root=overlay)

    # Prune-only paths stay gone; replaced rules may be delete+regraft on re-apply.
    plugin_root = cursor_dir / ECOSYSTEM_PLUGIN_RELPATH
    for relpath in PRUNE_ONLY_PLUGIN_PATHS:
        assert not (plugin_root / relpath).exists()
    assert grafted
    assert "landed is not live" in (
        plugin_root / "rules" / "restart-drain-discipline_ulg.mdc"
    ).read_text(encoding="utf-8")


def test_missing_overlay_fails_closed(tmp_path: Path) -> None:
    cursor_dir = _fake_dispatch_cursor_dir(tmp_path)
    with pytest.raises(SeatOverlayConfigError, match="overlay root absent"):
        apply_cursor_sdk_seat_overlay(cursor_dir, overlay_root=tmp_path / "nope")


def test_missing_plugin_copy_fails_closed(tmp_path: Path) -> None:
    bare = tmp_path / ".cursor"
    bare.mkdir()
    with pytest.raises(SeatOverlayConfigError, match="ecosystem plugin absent"):
        apply_cursor_sdk_seat_overlay(bare, overlay_root=_fake_overlay(tmp_path))


def test_repo_overlay_sot_is_present_and_named() -> None:
    """The real overlay SoT ships the interagent rule + skill this design depends on."""
    root = seat_overlay_root()
    assert (root / "rules" / "interagent-posture_ulg.mdc").is_file(), root
    assert (root / "skills" / "interagent-posture" / "SKILL.md").is_file(), root
    body = (root / "skills" / "interagent-posture" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "agent_seat" in body

    # 2026-09-01 (alwaysApply rules-thinning G3): operator-request-front-door /
    # judgment-escalation-ladder / lean-context-dispatch-first merged into
    # dispatch-kernel_ulg.mdc upstream — their seat-overlay grafts were removed
    # rather than left pointing at a dead filename.
    assert not (root / "rules" / "operator-request-front-door_ulg.mdc").exists()
    assert not (root / "rules" / "judgment-escalation-ladder_ulg.mdc").exists()
    assert not (root / "rules" / "lean-context-dispatch-first_ulg.mdc").exists()

    drain = (root / "rules" / "restart-drain-discipline_ulg.mdc").read_text(
        encoding="utf-8"
    )
    assert "restart_intent_id" in drain
    assert "Landed ≠ live" in drain
    assert "live@<sha>" in drain
    assert "code_ref_satisfied" in drain
    assert "caller_must_exit" not in drain  # lead-envelope vocabulary stays out

    # The one surviving skill-surface duty: SoT edit implies install in the same run.
    surface = (root / "rules" / "skill-surface_ulg.mdc").read_text(encoding="utf-8")
    assert "install-ecosystem-plugin.sh" in surface
    assert "Reload Window" not in surface.split("## Invariant", 1)[1]


def test_merged_kernels_stay_resident_no_seat_variant() -> None:
    """G6 verdict: the two merged kernels bind on this seat, so nothing shadows them.

    :func:`_graft_entries` globs every overlay ``*.mdc``, so dropping either filename
    into the seat tree would silently begin grafting a thinned body over a kernel the
    seat actually acts on. This pins the adjudicated decision rather than the
    filesystem's current shape — see the G6 note in :mod:`cursor_seat_overlay`.
    """
    root = seat_overlay_root()
    for resident in ("dispatch-kernel_ulg.mdc", "in-flight-work-guard_ulg.mdc"):
        assert not (root / "rules" / resident).exists(), resident
        assert Path("rules") / resident not in PRUNED_PLUGIN_PATHS, resident
        assert Path("rules") / resident not in LEAD_ONLY_PLUGIN_PATHS, resident
