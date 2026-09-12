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
