"""Unit + agreement tests for the shared contract vocabulary."""

from __future__ import annotations

from pathlib import Path

from contract_vocab import (
    CANONICAL_CONTRACTS,
    DEFAULT_CONTRACT,
    REMOVED_JOB_ALIASES,
    closeout_table,
    code_work_contracts,
    nested_scope_contracts,
    vision_required_admit_disclosure,
    vision_required_contracts,
    vocab_line,
)

_REPO = Path(__file__).resolve().parents[2]

_PROSE_SITES = (
    "services/mcp-server/tools/cursor_request.py",
    "services/mcp-server/tools/agent_bus/__init__.py",
    "services/mcp-server/tools/agent_bus/request.py",
    "services/mcp-server/tools/_oc_knowledge_templates.py",
)


def test_canonical_names_stable() -> None:
    assert CANONICAL_CONTRACTS == (
        "answer",
        "confer",
        "ask",
        "investigate",
        "implement",
        "verify",
        "execute",
        "propagate",
        "seed",
        "recon",
    )
    assert DEFAULT_CONTRACT == "answer"
    assert REMOVED_JOB_ALIASES == {"consult": "confer"}
    assert "hop" not in CANONICAL_CONTRACTS


def test_flag_sets_match_pre_consolidation_frozensets() -> None:
    assert nested_scope_contracts() == frozenset(
        {"implement", "investigate", "verify", "seed", "recon"}
    )
    assert vision_required_contracts() == frozenset(
        {"implement", "investigate", "seed", "recon"}
    )
    assert code_work_contracts() == frozenset(
        {"implement", "investigate", "verify", "seed", "recon"}
    )


def test_operator_renderers_cover_every_name() -> None:
    line = vocab_line()
    table = closeout_table()
    for name in CANONICAL_CONTRACTS:
        assert name in line
        assert f"| {name} |" in table


def test_handwritten_prose_sites_name_every_canonical_contract() -> None:
    for rel in _PROSE_SITES:
        text = (_REPO / rel).read_text(encoding="utf-8")
        missing = [name for name in CANONICAL_CONTRACTS if name not in text]
        assert not missing, f"{rel} missing {missing}"


def test_wire_map_contract_literal_lists_canonical_names() -> None:
    text = (
        _REPO / "services/git_integration_worker/cursor_auto/wire_map.py"
    ).read_text(encoding="utf-8")
    block = text.split("Contract = Literal[", 1)[1].split("]", 1)[0]
    for name in CANONICAL_CONTRACTS:
        assert f'"{name}"' in block
    assert "hop" not in block






def test_vision_required_admit_disclosure_lists_all_enforced_contracts() -> None:
    disclosure = vision_required_admit_disclosure()
    wire = vision_required_admit_disclosure(wire_style=True)
    for name in vision_required_contracts():
        assert name in disclosure
        assert name in wire
    stale = "contract ∈ {implement, investigate}"
    assert stale not in disclosure


