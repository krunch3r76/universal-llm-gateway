"""Falsifier for dispatch-role advertisement drift (friction 16278 / 13744).

Three guarantees:
  1. Working tree is drift-free (gen --check semantics inside pytest).
  2. Injected roster drift turns the gate red (detection, not just regeneration).
  3. A deleted/reworded anchor fails loudly (no vacuous pass).
"""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "gen-mcp-dispatch-role-docs"


def _load_gen_module():
    loader = SourceFileLoader("gen_role_docs", str(_SCRIPT))
    spec = importlib.util.spec_from_loader("gen_role_docs", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_role_doc_advertisements_drift_free() -> None:
    """Every gated advertisement in the working tree matches the catalog render."""
    mod = _load_gen_module()
    assert mod.main(check=True) == 0


def test_injected_roster_drift_detected() -> None:
    """Dropping web-implement from the negation line is caught by the render."""
    mod = _load_gen_module()
    src = (_REPO_ROOT / "config" / "mcp" / "canonical.yaml").read_text(encoding="utf-8")
    stale = src.replace("¬ web-consult/web-implement/", "¬ web-consult/", 1)
    assert stale != src
    assert mod._render_canonical(stale) != stale


def test_missing_anchor_fails_loudly() -> None:
    """Deleting the negation line entirely must raise, not silently pass."""
    mod = _load_gen_module()
    src = (_REPO_ROOT / "config" / "mcp" / "canonical.yaml").read_text(encoding="utf-8")
    gutted = "\n".join(ln for ln in src.splitlines() if "(handoff-only)" not in ln)
    with pytest.raises(SystemExit):
        mod._render_canonical(gutted)
