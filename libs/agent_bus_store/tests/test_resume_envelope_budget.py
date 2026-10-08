"""Resume envelope degrades — never fails — when one harvested message outsizes the fence budget.

Specimen: agent-bus:10479 2026-09-12 05:34Z — ``POST /threads/10479/resume-fence`` 500
``TapeBudgetExceeded: Tape budget 24000 cannot fit minimum viable pour (need 80500 …)``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store import resume_envelope as env_mod
from agent_bus_store.tape_degrade import TapeBudgetExceeded

pytestmark = pytest.mark.offline


def _raise_budget(**_: object) -> dict:
    raise TapeBudgetExceeded(
        thread_id="10479",
        budget_bytes=24000,
        required_budget_bytes=83404,
        min_viable_budget_bytes=80500,
    )


def test_envelope_degrades_to_zero_bodies_on_budget_overflow() -> None:
    with (
        patch.object(env_mod, "render_tape_with_harvest", side_effect=_raise_budget),
        patch.object(
            env_mod, "_tip_checkpoint_body", return_value=(143, "## Residue\nx")
        ),
        patch.object(env_mod, "_read_l3_summary_row", return_value=(None, None)),
    ):
        envelope = env_mod.build_resume_envelope("10479", tape_budget_bytes=24000)

    assert "error" not in envelope
    assert envelope["tape_verbal"] == []
    assert envelope["message_count"] == 0
    assert envelope["tape_truncated"] is True
    degraded = envelope["tape_degraded"]
    assert degraded["error"] == "tape_budget_exceeded"
    assert degraded["min_viable_budget_bytes"] == 80500
    assert degraded["overflow_uri"] == "/threads/10479/tape?budget_bytes=83404"
    assert envelope["scope"] == "last_session"


def test_envelope_degrades_on_utf8_slice_error() -> None:
    with (
        patch.object(
            env_mod,
            "render_tape_with_harvest",
            side_effect=UnicodeDecodeError("utf-8", b"\xe2", 0, 1, "unexpected end"),
        ),
        patch.object(
            env_mod, "_tip_checkpoint_body", return_value=(232, "## Residue\nx")
        ),
        patch.object(env_mod, "_read_l3_summary_row", return_value=(None, None)),
    ):
        envelope = env_mod.build_resume_envelope("10479")

    assert "error" not in envelope
    assert envelope["tape_verbal"] == []
    assert envelope["tape_degraded"]["error"] == "tape_utf8_truncated"


def test_envelope_reports_no_degradation_on_normal_pour() -> None:
    tape = {
        "messages": [],
        "open_line": {"scope": "last_session"},
        "truncated": False,
    }
    with (
        patch.object(env_mod, "render_tape_with_harvest", return_value=tape),
        patch.object(env_mod, "_tip_checkpoint_body", return_value=(1, "")),
        patch.object(env_mod, "_read_l3_summary_row", return_value=(None, None)),
    ):
        envelope = env_mod.build_resume_envelope("10479", tape_budget_bytes=24000)

    assert envelope["tape_degraded"] is None
    assert envelope["tape_truncated"] is False


def test_consolidate_summary_row_from_card_only_house(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A house whose only card is ``{id}-card.md`` still fills the L3 row."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    card = tmp_path / "notes/system/threads/10479-card.md"
    card.parent.mkdir(parents=True)
    card.write_text("**Settled:** card-only house is live\n", encoding="utf-8")
    tape = {
        "messages": [],
        "open_line": {"scope": "last_session"},
        "truncated": False,
    }
    with (
        patch.object(env_mod, "render_tape_with_harvest", return_value=tape),
        patch.object(env_mod, "_tip_checkpoint_body", return_value=(12, "")),
    ):
        envelope = env_mod.build_resume_envelope("10479", tape_budget_bytes=24000)

    assert envelope["consolidate_summary_row"] == "card-only house is live"
    assert envelope["summary_row_source"] == "l3_continuity_card"
    assert envelope["summary_row_as_of_turn"] == 12


def test_l3_summary_row_dual_file_reads_continuity_md(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Settled lives on -continuity.md when -card.md is also present."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    threads = tmp_path / "notes/system/threads"
    threads.mkdir(parents=True)
    (threads / "9582-card.md").write_text("# live card\n", encoding="utf-8")
    (threads / "9582-continuity.md").write_text(
        "**Settled:** fold wrote the archive\n",
        encoding="utf-8",
    )
    row, source = env_mod._read_l3_summary_row("9582")
    assert row == "fold wrote the archive"
    assert source == "l3_continuity_card"


def test_l3_summary_row_dual_file_prefers_card_md(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Settled on -card.md wins over a later -continuity.md line."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    threads = tmp_path / "notes/system/threads"
    threads.mkdir(parents=True)
    (threads / "9582-card.md").write_text(
        "**Settled:** live card row\n",
        encoding="utf-8",
    )
    (threads / "9582-continuity.md").write_text(
        "**Settled:** archive row\n",
        encoding="utf-8",
    )
    row, source = env_mod._read_l3_summary_row("9582")
    assert row == "live card row"
    assert source == "l3_continuity_card"


def test_l3_summary_row_reads_continuity_card_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    card = tmp_path / "notes/system/threads/10479-continuity-card.md"
    card.parent.mkdir(parents=True)
    card.write_text("**Live:** continuity-card name\n", encoding="utf-8")
    row, source = env_mod._read_l3_summary_row("10479")
    assert row == "continuity-card name"
    assert source == "l3_continuity_card"
