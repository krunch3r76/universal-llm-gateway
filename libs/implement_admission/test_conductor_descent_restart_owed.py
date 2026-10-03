"""Descent walk and restart_owed closeout line (friction a:37762)."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.conductor_descent import LineageView, descends_from_conductor
from implement_admission.conductor_materialize import (
    RematerializeContext,
    materialize_conductor,
)
from implement_admission.conductor_no_restart import NO_RESTART_LINE
from implement_admission.restart_owed import restart_owed_line
from services.git_integration_worker.cursor_sdk_packet import resolve_prompt_preamble

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[2]
_SKILL_REFS = (
    _REPO / "cursor-plugins/ulg-ecosystem/skills/conductor/reference-packet.md",
    _REPO
    / "cursor-plugins/ulg-ecosystem/skills/conductor/reference-run-to-completion.md",
)


class _StubCortex:
    def entity_get(self, entity_id: str, **kwargs: object) -> dict:
        return {
            "id": entity_id,
            "name": "Layer conductor unify",
            "attributes": {"density_triage": "judgment_required"},
        }

    def list_relationships(self, entity_id: str, *, type_id: str | None = None) -> list:
        _ = entity_id, type_id
        return []


def test_two_level_nest_and_hop_successor() -> None:
    rows = {
        "leaf": LineageView(contract="implement", nest_under="mid"),
        "mid": LineageView(contract="implement", nest_under="cond"),
        "cond": LineageView(contract="conductor"),
        "hop": LineageView(contract="freeform", hop_from="cond"),
        "plain": LineageView(contract="implement"),
    }

    def lookup(dispatch_id: str) -> LineageView | None:
        return rows.get(dispatch_id)

    assert descends_from_conductor("leaf", lookup) is True
    assert descends_from_conductor("hop", lookup) is True
    assert descends_from_conductor("cond", lookup) is True
    assert descends_from_conductor("plain", lookup) is False
    assert descends_from_conductor("", lookup) is False


def test_restart_owed_event_service_ignores_unrelated_docs() -> None:
    text = restart_owed_line(
        [
            "services/event-service/routes/query.py",
            "docs/architecture/overview.md",
        ]
    )
    assert text.splitlines()[0] == "restart_owed: event_service"
    assert "stargate" not in text
    assert "mcp" not in text
    assert "unmapped:" not in text


def test_restart_owed_docs_only_is_none() -> None:
    assert restart_owed_line(["docs/guide.md", "README.md"]) == "restart_owed: none"


def test_restart_owed_charter_runner_script_is_unmapped() -> None:
    path = "scripts/model_manager/ui/controller/charter_runner/propagation_execute.py"
    text = restart_owed_line([path])
    assert text.startswith("restart_owed: none\n")
    assert f"unmapped: {path}" in text


def test_restart_owed_unmapped_service_path_is_reported() -> None:
    text = restart_owed_line(["services/not-a-fleet-service/app.py"])
    assert text.startswith("restart_owed: none\n")
    assert "unmapped: services/not-a-fleet-service/app.py" in text


def test_materialized_packet_carries_no_restart_line(tmp_path: Path) -> None:
    mp = materialize_conductor(
        "todo:layer-conductor-unify",
        cortex=_StubCortex(),
        out_dir=tmp_path / "packets",
        files_root=tmp_path / "cortex",
    )
    assert NO_RESTART_LINE in mp.text
    assert "restart_owed:" in mp.text
    assert "Every nested implement and land prompt" in mp.text
    rematerialized = materialize_conductor(
        "todo:layer-conductor-unify",
        cortex=_StubCortex(),
        out_dir=tmp_path / "packets-hop",
        files_root=tmp_path / "cortex-hop",
        rematerialize=RematerializeContext(hop_seq=2, predecessor_dispatch_id="pred-1"),
    )
    assert NO_RESTART_LINE in rematerialized.text


def test_conductor_preamble_repeats_no_restart_line() -> None:
    text = resolve_prompt_preamble(
        handoff_contract="conductor",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        dispatch_id="cond-hop",
        has_packet_path=True,
        thread_id="14965",
        hop_seq=2,
        hop_from="pred-1",
    )
    assert NO_RESTART_LINE in text
    assert "restart_owed:" in text


def test_skill_nested_dispatch_examples_carry_no_restart_line() -> None:
    for path in _SKILL_REFS:
        body = path.read_text(encoding="utf-8")
        assert NO_RESTART_LINE in body, path.name
    packet = _SKILL_REFS[0].read_text(encoding="utf-8")
    assert "G7 land nest" in packet
    assert "contract=implement" in packet
