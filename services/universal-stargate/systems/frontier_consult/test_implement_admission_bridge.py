"""Regression: StargateCortexReader must satisfy implement_admission CortexReader.

Handoff tests stub their own entity_get, so they cannot catch a missing method on
the production reader class. See tasks/specs/implement-input-schema.md §4.1.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from implement_admission.normalize import CortexReader

from systems.frontier_consult.implement_admission_bridge import _repo_base
from systems.frontier_consult.stargate_cortex_reader import StargateCortexReader


def test_stargate_cortex_reader_exposes_entity_get() -> None:
    assert hasattr(StargateCortexReader, "entity_get")
    assert callable(getattr(StargateCortexReader, "entity_get"))
    assert hasattr(StargateCortexReader, "list_relationships")


def test_stargate_cortex_reader_usable_as_cortex_reader() -> None:
    reader = StargateCortexReader()

    def _accepts(reader: CortexReader) -> None:
        assert callable(reader.entity_get)

    _accepts(reader)


def test_stargate_cortex_reader_entity_get_accepts_entity_id_kw() -> None:
    """entity_get must mirror assertion_state dispatch shape (entity_id first)."""
    sig = inspect.signature(StargateCortexReader.entity_get)
    params = list(sig.parameters)
    assert params[0] == "self"
    assert params[1] == "entity_id"


def test_repo_base_no_double_nest_when_root_already_named_repo(tmp_path: Path) -> None:
    outer = tmp_path / "universal-llm-gateway"
    outer.mkdir()
    nested = outer / "universal-llm-gateway"
    nested.mkdir()
    assert _repo_base(outer) == outer.resolve()


class _BridgeStubCortex:
    def entity_get(self, entity_id: str, **kwargs: object) -> dict:
        return {
            "id": entity_id,
            "name": "Bridge house",
            "attributes": {"density_triage": "judgment_required"},
        }

    def list_relationships(self, *args: object, **kwargs: object) -> list:
        return []


def test_bridge_matches_materialize_house_block(
    pinned_cdp_roles,
    house_watch_dir,
    tmp_path: Path,
) -> None:
    from implement_admission.conductor_context import RematerializeContext
    from implement_admission.conductor_materialize import materialize_conductor
    from implement_admission.house_testing import HOUSE_MODEL, assert_house_packet_clean, write_tick

    from systems.frontier_consult.implement_admission_bridge import (
        resolve_source_ref_to_packet,
    )

    write_tick(
        house_watch_dir,
        "bridge-tid",
        {"induction_binds": ["NO cdp/*"], "critic_model": HOUSE_MODEL},
    )
    mat = materialize_conductor(
        "todo:bridge-house",
        cortex=_BridgeStubCortex(),
        out_dir=tmp_path / "mat",
        files_root=tmp_path,
        summoning_thread_id="bridge-tid",
    )
    br = resolve_source_ref_to_packet(
        "todo:bridge-house",
        cortex=_BridgeStubCortex(),  # type: ignore[arg-type]
        workspaces_root=tmp_path,
        contract="conductor",
        summoning_thread_id="bridge-tid",
    )
    assert br.packet_markdown is not None
    for text in (mat.text, br.packet_markdown):
        assert (
            "House binds (summoning thread" in text
            or "House policy unresolved for summoning thread" in text
        )
    assert_house_packet_clean(br.packet_markdown)


def test_hop_end_to_end_rereads_tick(
    pinned_cdp_roles,
    house_watch_dir,
    tmp_path: Path,
) -> None:
    from implement_admission.conductor_context import rematerialize_context_from_dispatch
    from implement_admission.house_testing import HOUSE_MODEL, assert_house_packet_clean, write_tick

    from systems.frontier_consult.implement_admission_bridge import (
        resolve_source_ref_to_packet,
    )

    tid = "e2e-hop"
    write_tick(
        house_watch_dir,
        tid,
        {"induction_binds": ["BIND-A"], "critic_model": HOUSE_MODEL},
    )
    rem_ctx = rematerialize_context_from_dispatch(
        hop_seq=1,
        hop_from="pred-1",
        dispatch_thread_id=tid,
        generation_options={"summoning_thread_id": tid},
    )
    br1 = resolve_source_ref_to_packet(
        "todo:e2e-hop",
        cortex=_BridgeStubCortex(),  # type: ignore[arg-type]
        workspaces_root=tmp_path,
        contract="conductor",
        summoning_thread_id=tid,
        rematerialize=rem_ctx,
    )
    assert br1.packet_markdown and "BIND-A" in br1.packet_markdown
    write_tick(
        house_watch_dir,
        tid,
        {"induction_binds": ["BIND-B"], "critic_model": HOUSE_MODEL},
    )
    br2 = resolve_source_ref_to_packet(
        "todo:e2e-hop",
        cortex=_BridgeStubCortex(),  # type: ignore[arg-type]
        workspaces_root=tmp_path,
        contract="conductor",
        summoning_thread_id=tid,
        rematerialize=rem_ctx,
    )
    assert br2.packet_markdown
    assert "BIND-B" in br2.packet_markdown
    assert "BIND-A" not in br2.packet_markdown
    assert_house_packet_clean(br1.packet_markdown)
    assert_house_packet_clean(br2.packet_markdown)


def test_watch_dir_isolation_active_sg(tmp_path: Path) -> None:
    import bus_watch.fable_lock

    assert str(bus_watch.fable_lock.WATCH_DIR).startswith(str(tmp_path))
