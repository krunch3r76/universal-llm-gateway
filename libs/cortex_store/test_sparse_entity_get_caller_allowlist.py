"""Static guard — execute_op(\"entity_get\") call sites (sparse gate item 6)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Paths may omit repo root prefix; normalize to relative strings.
_EXECUTE_OP_ENTITY_GET_ALLOWLIST: frozenset[str] = frozenset(
    {
        "libs/cortex_store/test_entity_preview_sparse_default.py",
        "libs/cortex_store/test_entity_get_dispatch_telemetry.py",
        "libs/cortex_store/test_entity_get_body_intent.py",
        "libs/cortex_store/test_entity_get_runbook_inline_body.py",
        "libs/cortex_store/dispatch_ops/test_shadow_validate.py",
        "libs/cortex_store/dispatch_ops/test_offload_hint.py",
        "libs/cortex_store/test_entity_get_batch.py",
        "libs/cortex_store/test_entity_get_expected_revision_contract.py",
        "tests/implement_admission/test_implement_ready_skeptic.py",
        "libs/cortex_store/test_todo_distill_implement_gate.py",
        "scripts/cortex/migrate_case_discovery_waves_abc.py",
        "scripts/cortex/retire_matter_skills_wave_bc.py",
        "libs/web_chat_relay/orion_evidence_card.py",
    }
)


def _iter_py_files() -> list[Path]:
    out: list[Path] = []
    for name in ("services", "libs", "tests", "scripts"):
        root = _REPO_ROOT / name
        if not root.is_dir():
            continue
        out.extend(p for p in root.rglob("*.py") if "tmp" not in p.parts)
    return out


def _file_calls_entity_get_execute_op(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "execute_op":
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and first.value == "entity_get":
                return True
    return False


@pytest.mark.offline
def test_execute_op_entity_get_callers_match_allowlist() -> None:
    found: set[str] = set()
    for py in _iter_py_files():
        if not _file_calls_entity_get_execute_op(py):
            continue
        rel = str(py.relative_to(_REPO_ROOT))
        found.add(rel)
    assert found == _EXECUTE_OP_ENTITY_GET_ALLOWLIST, (
        "Update _EXECUTE_OP_ENTITY_GET_ALLOWLIST after intentional new "
        f"execute_op('entity_get') sites; found={sorted(found)}"
    )
