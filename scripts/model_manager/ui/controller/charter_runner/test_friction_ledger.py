"""Friction ledger enroll_state derivation."""

from __future__ import annotations

from scripts.model_manager.ui.controller.charter_runner.friction_ledger import (
    FrictionLedgerRow,
    build_ledger,
    derive_enroll_state,
)


def test_derive_queued_for_open_follow_on() -> None:
    state = derive_enroll_state(
        attrs={"actionable": True},
        root_has_charter_runner=True,
        root_conveyor_off=False,
        open_todo_slug="todo:friction-1-test",
        any_todo_slug="todo:friction-1-test",
    )
    assert state == "queued"


def test_derive_minted_only_for_closed_todo() -> None:
    state = derive_enroll_state(
        attrs={"actionable": True},
        root_has_charter_runner=True,
        root_conveyor_off=False,
        open_todo_slug=None,
        any_todo_slug="todo:friction-2-test",
    )
    assert state == "minted_only"


def test_derive_filed_only_for_actionable_no_todo() -> None:
    state = derive_enroll_state(
        attrs={"actionable": True},
        root_has_charter_runner=True,
        root_conveyor_off=False,
        open_todo_slug=None,
        any_todo_slug=None,
    )
    assert state == "filed_only"


def test_derive_omits_non_actionable_without_todo() -> None:
    state = derive_enroll_state(
        attrs={"actionable": False},
        root_has_charter_runner=True,
        root_conveyor_off=False,
        open_todo_slug=None,
        any_todo_slug=None,
    )
    assert state is None


def test_derive_opted_out_on_defer_enqueue() -> None:
    state = derive_enroll_state(
        attrs={"defer_enqueue": True, "actionable": True},
        root_has_charter_runner=True,
        root_conveyor_off=False,
        open_todo_slug=None,
        any_todo_slug=None,
    )
    assert state == "opted_out"


def test_derive_minted_only_when_conveyor_off() -> None:
    state = derive_enroll_state(
        attrs={"actionable": True},
        root_has_charter_runner=True,
        root_conveyor_off=True,
        open_todo_slug="todo:friction-3-test",
        any_todo_slug="todo:friction-3-test",
    )
    assert state == "minted_only"


def test_derive_minted_only_without_charter_runner_tag() -> None:
    state = derive_enroll_state(
        attrs={"actionable": True},
        root_has_charter_runner=False,
        root_conveyor_off=False,
        open_todo_slug="todo:friction-4-test",
        any_todo_slug="todo:friction-4-test",
    )
    assert state == "minted_only"


def test_build_ledger_omits_non_actionable_without_todo() -> None:
    rows = [
        {
            "id": 101,
            "claim": "[protocol] ceremonial note",
            "attributes": {
                "charter_root": "9001",
                "actionable": False,
            },
        }
    ]

    def _frictions(**kwargs: object) -> dict:
        return {"items": rows}

    import scripts.model_manager.ui.controller.charter_runner.friction_ledger as mod

    original_open = mod.todo_open_for_friction
    original_exists = mod.todo_exists_for_friction
    mod.todo_open_for_friction = lambda aid: None  # type: ignore[assignment]
    mod.todo_exists_for_friction = lambda aid: None  # type: ignore[assignment]
    try:
        ledger = build_ledger("9001", root_tags=["charter-runner"], frictions_fn=_frictions)
    finally:
        mod.todo_open_for_friction = original_open
        mod.todo_exists_for_friction = original_exists

    assert ledger == []


def test_build_ledger_queued_with_open_todo() -> None:
    rows = [
        {
            "id": 102,
            "claim": "[protocol] deploy probe failed",
            "attributes": {
                "charter_root": "9001",
                "actionable": True,
            },
        }
    ]

    def _frictions(**kwargs: object) -> dict:
        return {"items": rows}

    import scripts.model_manager.ui.controller.charter_runner.friction_ledger as mod

    original_open = mod.todo_open_for_friction
    mod.todo_open_for_friction = lambda aid: "todo:friction-102-deploy" if aid == 102 else None  # type: ignore[assignment]
    try:
        ledger = build_ledger(
            "9001",
            root_tags=["charter-runner"],
            frictions_fn=_frictions,
        )
    finally:
        mod.todo_open_for_friction = original_open

    assert len(ledger) == 1
    assert ledger[0] == FrictionLedgerRow(
        assertion_id=102,
        note="deploy probe failed",
        enroll_state="queued",
        todo_slug="todo:friction-102-deploy",
    )
