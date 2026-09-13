"""Wake induction: planted addresses, episodic frame, cap, quiet form."""

from __future__ import annotations

from bus_watch.induction import INDUCTION_CAP, build_wake_induction


def _digest(**over):  # noqa: ANN003, ANN202
    base = {
        "ts": "2026-09-13T04:10:00Z",
        "root": {
            "id": "10479",
            "turns": 214,
            "last_subject": "CHECKPOINT 10479 54b93098",
        },
        "register": "autonomous",
        "attention": [
            {
                "id": "10586",
                "unread": 1,
                "last_subject": "cursor-sdk CLOSEOUT 824494a8ceba",
            },
            {"kind": "budget_estimate", "used_tokens": 1, "window_limit_tokens": 2},
        ],
        "watchers_complete_unrelayed": [
            {"label": "10479-10586-r9-none", "thread": "10586"}
        ],
        "budget": {"stop_class": None},
        "checkpoint_due": False,
        "changed_since_last_tick": True,
        "summary_row": "R9 Lane A serialization (design, a:33072)",
        "policy": {
            "induction_binds": ["hopper paused (10479#210)"],
            "induction_loaded": ["git-posture § Land"],
        },
    }
    base.update(over)
    return base


def test_induction_plants_addresses_not_a_skill_copy() -> None:
    text = build_wake_induction(_digest())
    assert text.startswith("WAKE 10479 · turns=214")
    assert "Event: 10586 unread=1" in text
    assert "watcher 10479-10586-r9-none complete" in text
    assert "NOW: R9 Lane A serialization" in text
    assert "do not re-read): liaison skill · git-posture § Land" in text
    assert "register=autonomous · hopper paused (10479#210)" in text
    assert "cdp/opus-5 first" in text
    assert len(text.encode("utf-8")) <= INDUCTION_CAP


def test_induction_names_context_budget_and_checkpoint() -> None:
    text = build_wake_induction(
        _digest(
            budget={
                "stop_class": "CONTEXT_BUDGET",
                "used_tokens": 220_000,
                "window_limit_tokens": 256_000,
                "source": "ide.transcript",
            },
            checkpoint_due=True,
        )
    )
    assert "CONTEXT_BUDGET 86% (ide.transcript) → CHECKPOINT, then hop or PARK" in text
    assert "CHECKPOINT due" in text


def test_induction_empty_now_is_not_a_stop() -> None:
    text = build_wake_induction(_digest(summary_row=None))
    assert "empty NOW is not a stop" in text


def test_induction_now_from_policy_bind() -> None:
    text = build_wake_induction(
        _digest(summary_row=None, policy={"now_row": "R10 wake induction transport"})
    )
    assert "NOW: R10 wake induction transport" in text


def test_induction_quiet_form() -> None:
    text = build_wake_induction(
        _digest(
            attention=[], watchers_complete_unrelayed=[], changed_since_last_tick=False
        )
    )
    assert text.startswith("QUIET 10479")
    assert "one line, end turn" in text


def test_induction_respects_cap() -> None:
    lanes = [
        {"id": str(20000 + i), "unread": 3, "last_subject": "x" * 56} for i in range(12)
    ]
    text = build_wake_induction(_digest(attention=lanes), cap=400)
    assert len(text.encode("utf-8")) <= 400
    assert text.startswith("WAKE 10479")
    assert "NOW:" in text
