"""Unit tests for thread_compression boundary helpers."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

_REPO = pathlib.Path(__file__).resolve().parents[1]
_PKG = "systems.pipeline.core.handlers.thread_persistence"

for _p in (
    str(_REPO / "libs"),
    str(_REPO / "services" / "universal-stargate"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

for _name in (
    "systems",
    "systems.pipeline",
    "systems.pipeline.core",
    "systems.pipeline.core.handlers",
    _PKG,
):
    if _name not in sys.modules:
        _m = ModuleType(_name)
        _m.__path__ = []  # type: ignore[attr-defined]
        _m.__package__ = _name
        sys.modules[_name] = _m

_events_stub = ModuleType(f"{_PKG}.events")
_events_stub.cx_async = None  # type: ignore[attr-defined]
sys.modules[f"{_PKG}.events"] = _events_stub

_TP = (
    _REPO
    / "services/universal-stargate/systems/pipeline/core/handlers/thread_persistence"
)


def _load(name: str, filename: str) -> ModuleType:
    path = _TP / filename
    spec = importlib.util.spec_from_file_location(f"{_PKG}.{name}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG  # type: ignore[attr-defined]
    sys.modules[f"{_PKG}.{name}"] = mod
    spec.loader.exec_module(mod)  # type: ignore[arg-type]
    return mod


_ta = _load("turn_assertions", "turn_assertions.py")
_tc = _load("thread_compression", "thread_compression.py")

boundaries_from_exclusive_upper = _tc.boundaries_from_exclusive_upper
parse_thread_compression_boundaries = _tc.parse_thread_compression_boundaries
thread_compression_reasoning_summary = _tc.thread_compression_reasoning_summary


def test_boundaries_from_exclusive_upper() -> None:
    assert boundaries_from_exclusive_upper(5) == (4, 5)


def test_reasoning_summary_round_trip() -> None:
    raw = thread_compression_reasoning_summary(
        covered_through_turn_index=4,
        hot_tail_start_turn_index=5,
    )
    parsed = parse_thread_compression_boundaries(
        {"predicate_form": "thread_summary(5)", "reasoning_summary": raw}
    )
    assert parsed == (4, 5)


def test_predicate_form_fallback_without_reasoning_summary() -> None:
    assert parse_thread_compression_boundaries(
        {"predicate_form": "thread_summary(3)", "reasoning_summary": None}
    ) == (2, 3)
