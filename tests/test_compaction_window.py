"""Unit tests for thread_persistence/window.py — Phase 2 summary prepend.

Tests the Phase 2 contract for ``build_referential_window``:

- Without a consolidation summary → pure hot-tail (existing behaviour).
- With a summary → ``system``-role summary message prepended before the hot tail.
- ``k`` clipping applied to the hot tail only (summary is always prepended).
- Empty anchor → empty list (no summary, no turns).

Uses importlib to load ``window.py`` in isolation; the ``load_all_assertions``
dependency is patched per-test so no live cortex round-trip is needed.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Bootstrap — build the minimal package stub so ``from .turn_assertions import
# ...`` in window.py resolves without loading the full pipeline handler chain.
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_STARGATE_PATH = _REPO / "services" / "universal-stargate"
_HANDLERS_DIR = _STARGATE_PATH / "systems/pipeline/core/handlers"
_TP_DIR = _HANDLERS_DIR / "thread_persistence"

for _p in (
    str(_REPO / "libs"),
    str(_STARGATE_PATH),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_PKG = "systems.pipeline.core.handlers.thread_persistence"
for _name in (
    "systems",
    "systems.pipeline",
    "systems.pipeline.core",
    "systems.pipeline.core.handlers",
    _PKG,
):
    if _name not in sys.modules:
        _m = types.ModuleType(_name)
        _m.__path__ = []  # type: ignore[attr-defined]
        _m.__package__ = _name
        sys.modules[_name] = _m

# Stub events (needed by turn_assertions.py)
_events_stub = types.ModuleType(f"{_PKG}.events")
_events_stub.cx_async = None  # type: ignore[attr-defined]
sys.modules[f"{_PKG}.events"] = _events_stub


def _load_module(name: str, path: Path) -> types.ModuleType:
    """Load a module from an absolute path under the _PKG namespace."""
    full_name = f"{_PKG}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG  # type: ignore[attr-defined]
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_ta_mod = _load_module("turn_assertions", _TP_DIR / "turn_assertions.py")
_load_module("thread_compression", _TP_DIR / "thread_compression.py")
_window_mod = _load_module("window", _TP_DIR / "window.py")

build_referential_window = _window_mod.build_referential_window


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _turn(pred: str, claim: str) -> dict[str, Any]:
    return {"predicate_form": pred, "claim": claim, "superseded_by": None}


def _summary(turn_boundary: int) -> dict[str, Any]:
    return {
        "predicate_form": f"thread_summary({turn_boundary})",
        "claim": f"archive summary: Summary up to turn {turn_boundary}.",
        "superseded_by": None,
        "evidence_uris": [],
    }


def _patch_load_all(monkeypatch: pytest.MonkeyPatch, rows: list[dict]) -> None:
    """Replace ``load_all_assertions`` in the loaded window module."""

    async def _mock(anchor_id: str) -> list[dict]:
        return rows

    monkeypatch.setattr(_window_mod, "load_all_assertions", _mock)


# ---------------------------------------------------------------------------
# Tests — no summary (baseline)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_summary_returns_hot_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a summary, the result is the last k turn messages."""
    _patch_load_all(
        monkeypatch,
        [
            _turn("user_turn(0)", "User: hello"),
            _turn("assistant_turn(0)", "Assistant: world"),
        ],
    )
    result = await build_referential_window("anchor:1", k=8)
    assert result == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]


@pytest.mark.asyncio
async def test_no_summary_k_clips_hot_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """k=1 clips to the last turn pair (or last k messages)."""
    rows = []
    for i in range(5):
        rows.append(_turn(f"user_turn({i})", f"User: msg {i}"))
        rows.append(_turn(f"assistant_turn({i})", f"Assistant: reply {i}"))
    _patch_load_all(monkeypatch, rows)
    result = await build_referential_window("anchor:1", k=2)
    assert len(result) == 2
    # Last 2 messages = user_turn(4) and assistant_turn(4)
    assert result[0]["role"] == "user"
    assert result[0]["content"] == "msg 4"
    assert result[1]["role"] == "assistant"
    assert result[1]["content"] == "reply 4"


@pytest.mark.asyncio
async def test_empty_anchor_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_load_all(monkeypatch, [])
    result = await build_referential_window("anchor:1", k=8)
    assert result == []


# ---------------------------------------------------------------------------
# Tests — with summary (Phase 2 prepend)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_prepended_before_hot_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Summary message is prepended as ``system`` role before the hot tail."""
    _patch_load_all(
        monkeypatch,
        [
            _summary(2),
            _turn("user_turn(2)", "User: current"),
            _turn("assistant_turn(2)", "Assistant: response"),
        ],
    )
    result = await build_referential_window("anchor:1", k=8)
    assert len(result) == 3
    assert result[0]["role"] == "system"
    assert result[0]["content"].startswith("[Archive summary]\n")
    assert "Summary up to turn 2" in result[0]["content"]
    assert result[1] == {"role": "user", "content": "current"}
    assert result[2] == {"role": "assistant", "content": "response"}


@pytest.mark.asyncio
async def test_summary_only_no_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Summary with no active turns → list with only the summary message."""
    _patch_load_all(monkeypatch, [_summary(4)])
    result = await build_referential_window("anchor:1", k=8)
    assert len(result) == 1
    assert result[0]["role"] == "system"
    assert "Summary up to turn 4" in result[0]["content"]


@pytest.mark.asyncio
async def test_summary_k_clips_hot_tail_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """k=2 clips the hot tail but does NOT clip the prepended summary."""
    rows = [_summary(3)]
    for i in range(5):
        rows.append(_turn(f"user_turn({i})", f"User: {i}"))
        rows.append(_turn(f"assistant_turn({i})", f"Assistant: {i}"))
    _patch_load_all(monkeypatch, rows)
    result = await build_referential_window("anchor:1", k=2)
    # 1 summary + 2 hot-tail messages = 3 total
    assert len(result) == 3
    assert result[0]["role"] == "system"
    assert result[1]["role"] == "user"
    assert result[1]["content"] == "4"


@pytest.mark.asyncio
async def test_superseded_summary_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Superseded summary is not injected; result is pure hot tail."""
    superseded_summary = {
        "predicate_form": "thread_summary(3)",
        "claim": "archive summary: Old.",
        "superseded_by": 99,
        "evidence_uris": [],
    }
    _patch_load_all(
        monkeypatch,
        [
            superseded_summary,
            _turn("user_turn(3)", "User: hi"),
        ],
    )
    result = await build_referential_window("anchor:1", k=8)
    assert len(result) == 1
    assert result[0]["role"] == "user"


@pytest.mark.asyncio
async def test_summary_boundary_filters_older_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hot tail starts at summary exclusive boundary, not blind prefix[-k:]."""
    rows = [
        {
            "predicate_form": "thread_summary(3)",
            "claim": "archive summary: Up to turn 2.",
            "superseded_by": None,
            "evidence_uris": [],
            "reasoning_summary": (
                '{"covered_through_turn_index":2,"hot_tail_start_turn_index":3}'
            ),
        },
    ]
    for i in range(5):
        rows.append(_turn(f"user_turn({i})", f"User: {i}"))
        rows.append(_turn(f"assistant_turn({i})", f"Assistant: {i}"))
    _patch_load_all(monkeypatch, rows)
    result = await build_referential_window("anchor:1", k=8)
    assert result[0]["role"] == "system"
    user_contents = [m["content"] for m in result if m["role"] == "user"]
    assert "0" not in user_contents
    assert "1" not in user_contents
    assert "2" not in user_contents
    assert "3" in user_contents
    assert "4" in user_contents


@pytest.mark.asyncio
async def test_latest_of_multiple_summaries_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When multiple summaries exist, the one with the highest boundary is used."""
    _patch_load_all(
        monkeypatch,
        [
            _summary(2),
            _summary(5),  # highest — should win
            _summary(3),
        ],
    )
    result = await build_referential_window("anchor:1", k=8)
    assert len(result) == 1
    assert "Summary up to turn 5" in result[0]["content"]
