"""Tests for ImplementCloseout v1 and live adapter registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from implement_admission.closeout import (
    ADAPTERS,
    aggregate_adapter_status,
    apply_closeout,
    reconcile_closeout,
    run_adapters,
    run_composition,
)
from implement_admission.closeout_models import ImplementCloseout
from implement_admission.closeout_runtime import (
    CloseoutRuntime,
    reset_runtime,
    set_runtime,
)
from implement_admission.spec import (
    CloseoutAdapterKind,
    CloseoutStatus,
    Source,
    SourceKind,
)


@pytest.fixture(autouse=True)
def _reset_closeout_runtime() -> None:
    reset_runtime()
    ADAPTERS.clear()
    from implement_admission.closeout import _load_adapters

    ADAPTERS.update(_load_adapters())


def test_closeout_round_trip() -> None:
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="done",
        source_ref="todo:foo",
    )
    restored = ImplementCloseout.model_validate(closeout.model_dump())
    assert restored.source_ref == "todo:foo"


def test_adapter_registry_has_all_kinds() -> None:
    for kind in CloseoutAdapterKind:
        if kind == CloseoutAdapterKind.MIXED:
            continue
        assert kind.value in ADAPTERS


def test_no_stub_not_implemented_in_closeout_py() -> None:
    root = Path(__file__).resolve().parent
    text = (root / "closeout.py").read_text(encoding="utf-8")
    adapters_text = (root / "closeout_adapters.py").read_text(encoding="utf-8")
    assert "NotImplementedError" not in text
    assert "NotImplementedError" not in adapters_text


def test_todo_adapter_delegates_to_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def fake_pipeline(pipeline_id: str, options: dict) -> dict:
        captured["pipeline_id"] = pipeline_id
        captured["options"] = options
        return {"ok": True, "todo_id": options["todo_id"]}

    set_runtime(CloseoutRuntime(run_pipeline=fake_pipeline))
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="closed",
        source_ref="todo:my-todo",
    )
    source = Source(
        source_ref="todo:my-todo",
        canonical_ref="todo:my-todo",
        source_kind=SourceKind.TODO,
    )
    results = run_adapters(closeout, source)
    assert captured["pipeline_id"] == "todo-close"
    assert captured["options"]["todo_id"] == "todo:my-todo"
    assert "evidence_uris" in captured["options"]
    assert results[0].status == "complete"


def test_plan_phase_child_of_direction(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_dispatch(tool: str, args: dict) -> dict:
        calls.append((tool, args))
        if tool == "entity_create":
            return {"id": args["id"]}
        if tool == "relationship_create":
            return {"id": 99}
        return {}

    writes: list[Path] = []

    def fake_write(path: Path, content: str) -> None:
        writes.append(path)

    set_runtime(CloseoutRuntime(dispatch=fake_dispatch, write_text=fake_write))
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="phase done",
        source_ref="plan_phase:arc/phase-1",
        files_modified=["a.py"],
    )
    source = Source(
        source_ref="plan_phase:arc/phase-1",
        canonical_ref="plan_phase:arc/phase-1",
        parent_ref="plan:arc",
        selector="phase-1",
        source_kind=SourceKind.PLAN_PHASE,
    )
    results = run_adapters(closeout, source)
    rel_calls = [c for c in calls if c[0] == "relationship_create"]
    assert rel_calls
    assert rel_calls[0][1]["source_id"] == "plan_phase:arc/phase-1"
    assert rel_calls[0][1]["target_id"] == "plan:arc"
    assert rel_calls[0][1]["type_id"] == "child_of"
    assert "contains" not in str(rel_calls)
    assert results[0].status == "complete"


def test_plan_adapter_wrapup_and_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path))

    def fake_dispatch(tool: str, args: dict) -> dict:
        if tool == "relationships":
            return {"relationships": []}
        if tool == "entity_update":
            return {"workflow_state": "done"}
        return {}

    writes: list[Path] = []

    def fake_write(path: Path, content: str) -> None:
        writes.append(path)

    set_runtime(CloseoutRuntime(dispatch=fake_dispatch, write_text=fake_write))
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="arc done",
        source_ref="plan:my-arc",
    )
    source = Source(
        source_ref="plan:my-arc",
        canonical_ref="plan:my-arc",
        source_kind=SourceKind.PLAN,
    )
    results = run_adapters(closeout, source)
    assert results[0].status == "partial"
    assert any("00-my-arc-wrap-up.md" in str(p) for p in writes)


def test_composition_runs_embedded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path))
    packet_rel = "packets/p.md"
    packet = tmp_path / packet_rel
    packet.parent.mkdir(parents=True, exist_ok=True)
    packet.write_text("---\nsource_ref: todo:embedded\n---\n", encoding="utf-8")

    captured: dict = {}

    def fake_pipeline(pipeline_id: str, options: dict) -> dict:
        captured["todo_id"] = options.get("todo_id")
        return {"ok": True}

    set_runtime(CloseoutRuntime(run_pipeline=fake_pipeline))
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="packet close",
        source_ref=f"packet:{packet_rel}",
    )
    source = Source(
        source_ref=f"packet:{packet_rel}",
        canonical_ref=f"packet:{packet_rel}",
        source_kind=SourceKind.PACKET,
    )
    results = run_adapters(closeout, source)
    assert captured.get("todo_id") == "todo:embedded"
    adapters = {r.adapter for r in results}
    assert CloseoutAdapterKind.PACKET.value in adapters
    assert CloseoutAdapterKind.TODO.value in adapters


def test_mixed_partial_failure_no_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    class OkAdapter:
        def apply(self, closeout, *, source):  # noqa: ANN001
            from implement_admission.closeout_models import AdapterResult

            return [AdapterResult(adapter="todo", status="complete", mutation="ok")]

    class FailAdapter:
        def apply(self, closeout, *, source):  # noqa: ANN001
            from implement_admission.closeout_models import AdapterResult

            return [AdapterResult(adapter="plan", status="failed", error="boom")]

    ADAPTERS["todo"] = OkAdapter()
    ADAPTERS["plan"] = FailAdapter()
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:a",
    )
    sources = [
        Source(
            source_ref="todo:a",
            canonical_ref="todo:a",
            source_kind=SourceKind.TODO,
        ),
        Source(
            source_ref="plan:b",
            canonical_ref="plan:b",
            source_kind=SourceKind.PLAN,
        ),
    ]
    results = run_composition(closeout, sources)
    assert len(results) == 2
    assert aggregate_adapter_status([r.status for r in results]) == "partial"


def test_apply_returns_list_and_status_reconcile() -> None:
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:foo",
    )

    class ListAdapter:
        def apply(self, closeout, *, source):  # noqa: ANN001
            from implement_admission.closeout_models import AdapterResult

            return [
                AdapterResult(adapter="a", status="complete"),
                AdapterResult(adapter="b", status="partial", error="e"),
            ]

    ADAPTERS["todo"] = ListAdapter()
    results = run_adapters(
        closeout,
        Source(
            source_ref="todo:foo",
            canonical_ref="todo:foo",
            source_kind=SourceKind.TODO,
        ),
    )
    assert len(results) == 2
    reconciled = reconcile_closeout(closeout, results)
    assert reconciled.status == CloseoutStatus.PARTIAL
    assert len(reconciled.adapter_results) == 2


def test_mixed_not_reachable_via_sourcekind() -> None:
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:foo",
    )
    source = Source(
        source_ref="todo:foo",
        canonical_ref="todo:foo",
        source_kind=SourceKind.TODO,
    )
    assert source.source_kind != "mixed"
    with pytest.raises(ValueError):
        SourceKind("mixed")


def test_apply_closeout_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    set_runtime(CloseoutRuntime(run_pipeline=lambda _pid, _opts: {"ok": True}))
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="done",
        source_ref="todo:foo",
    )
    out = apply_closeout(closeout)
    assert out.adapter_results
    assert out.status in {CloseoutStatus.COMPLETE, CloseoutStatus.PARTIAL}
