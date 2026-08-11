"""Unit tests for scoreboard open propagation row rendering."""

from __future__ import annotations

from scripts.model_manager.ui.charter_scoreboard_propagation import (
    patch_scoreboard_open_rows,
    render_open_propagation_table,
    service_has_unprompted_settle_consumer,
)


def test_render_open_rows_includes_age_and_proof_class():
    table = render_open_propagation_table(
        [
            {
                "service": "git_integration_worker",
                "code_ref": "abc123",
                "safe_window": "drain_required",
                "age_in_harvests": 2,
                "proof_class": "process_live",
                "mint_thread": "6328",
                "mint_turn": 44,
            }
        ]
    )
    assert "git_integration_worker" in table
    assert "abc123" in table
    assert "| 2 |" in table or "| 2 | process_live |" in table
    assert "process_live" in table
    assert "6328 t44" in table


def test_patch_scoreboard_replaces_open_rows_section():
    original = """\
# Scoreboard

## Open propagation rows

| old | row |
|---|---|

## Ratified correction
text
"""
    patched = patch_scoreboard_open_rows(original, [])
    assert "| old | row |" not in patched
    assert "_none open_" in patched
    assert "## Ratified correction" in patched
    assert "## Open propagation obligations" in patched
    assert "not current liveness" in patched


def test_render_labels_obligations_not_liveness():
    table = render_open_propagation_table([])
    assert "## Open propagation obligations" in table
    assert "observe_code_ref_live" in table
    assert "not current liveness" in table
    assert "no obligation attempt recorded" in table
    assert "absence ≠ unserved" in table


def test_known_service_has_unprompted_consumer():
    assert service_has_unprompted_settle_consumer("mcp") is True


def test_no_consumer_service_gets_scoreboard_annotation():
    table = render_open_propagation_table(
        [
            {
                "service": "orphan_service",
                "code_ref": "abc123",
                "safe_window": "drain_required",
                "age_in_harvests": 1,
                "proof_class": "process_live",
            }
        ]
    )
    assert "no_unprompted_settle_consumer" in table
    assert "orphan_service" in table


def test_consumer_service_ordinary_rendering_without_annotation():
    table = render_open_propagation_table(
        [
            {
                "service": "git_integration_worker",
                "code_ref": "abc123",
                "safe_window": "drain_required",
                "age_in_harvests": 1,
                "proof_class": "process_live",
            }
        ]
    )
    assert "no_unprompted_settle_consumer" not in table
