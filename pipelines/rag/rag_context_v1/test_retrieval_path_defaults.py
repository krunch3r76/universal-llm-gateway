from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_retrieval_path_defaults_under_test",
    Path(__file__).resolve().parent / "retrieval_path_defaults.py",
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)
resolve_retrieval_path = _mod.resolve_retrieval_path


def test_runtime_retrieval_path_override_wins() -> None:
    assert (
        resolve_retrieval_path(
            runtime={"retrieval_path": "research"},
            effective={},
            scope_key="lighter",
        )
        == "research"
    )


def test_scope_specific_default_applies_when_omitted() -> None:
    assert (
        resolve_retrieval_path(
            runtime={},
            effective={},
            scope_key="lighter",
        )
        == "general"
    )


def test_global_default_is_general() -> None:
    assert (
        resolve_retrieval_path(
            runtime={},
            effective={},
            scope_key="research",
        )
        == "general"
    )
