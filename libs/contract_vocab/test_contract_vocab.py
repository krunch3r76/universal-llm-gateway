"""Unit + agreement tests for the shared contract vocabulary."""

from __future__ import annotations

from pathlib import Path

from contract_vocab import (
    CANONICAL_CONTRACTS,
    DEFAULT_CONTRACT,
    DEPRECATED_ALIASES,
    closeout_table,
    code_work_contracts,
    nested_scope_contracts,
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
        "investigate",
        "implement",
        "verify",
        "execute",
        "propagate",
        "seed",
        "recon",
    )
    assert DEFAULT_CONTRACT == "answer"
    assert DEPRECATED_ALIASES == {"consult": "confer"}
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


def test_consumer_flag_sets_agree_with_records() -> None:
    from services.git_integration_worker.cursor_auto.directive import (
        NESTED_SCOPE_CONTRACTS,
        VISION_REQUIRED_CONTRACTS,
    )
    from services.git_integration_worker.cursor_auto.episode_briefing import (
        _CODE_WORK_CONTRACTS,
    )
    from services.git_integration_worker.cursor_auto.wire_map import _CONTRACTS

    assert NESTED_SCOPE_CONTRACTS == nested_scope_contracts()
    assert VISION_REQUIRED_CONTRACTS == vision_required_contracts()
    assert _CODE_WORK_CONTRACTS == code_work_contracts()
    assert _CONTRACTS == frozenset(CANONICAL_CONTRACTS)


def test_handler_nested_contracts_are_nested_scope_plus_confer() -> None:
    from services.git_integration_worker.cursor_auto.handler import _NESTED_CONTRACTS

    assert _NESTED_CONTRACTS == nested_scope_contracts() | {"confer"}


def test_disposition_hints_cover_every_canonical_contract() -> None:
    from services.git_integration_worker.cursor_auto.wire_map import (
        resolve_contract_disposition,
    )

    hinted = {
        name: resolve_contract_disposition(name)["disposition_hint"]
        for name in CANONICAL_CONTRACTS
    }
    assert set(hinted) == set(CANONICAL_CONTRACTS)
    assert all(hinted.values())
