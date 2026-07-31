"""Parity: cursorbuild and bridge share one probe_working_tree implementation."""

from __future__ import annotations

import importlib

from admission_common.tree_probe import probe_working_tree as canonical_probe


def test_cursorbuild_uses_shared_probe():
    validator = importlib.import_module("cursorbuild.validator")
    assert validator.probe_working_tree is canonical_probe


def test_bridge_imports_shared_probe():
    bridge = importlib.import_module(
        "systems.frontier_consult.implement_admission_bridge"
    )
    assert bridge.probe_working_tree is canonical_probe
