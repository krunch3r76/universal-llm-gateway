"""Unit tests for Phase 2 — summary prepend in window.py and turn_assertions.py.

Bootstrap pattern mirrors test_compaction_summarize.py: importlib loads the
target modules with a minimal package stub so the ``from .events import cx_async``
relative import resolves without triggering the full pipeline __init__ chain.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, patch

# ---------------------------------------------------------------------------
# Bootstrap — inject minimal package hierarchy
# ---------------------------------------------------------------------------

_REPO = pathlib.Path(__file__).resolve().parents[1]

for _p in (
    str(_REPO / "libs"),
    str(_REPO / "services" / "universal-stargate"),
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
        _m = ModuleType(_name)
        _m.__path__ = []  # type: ignore[attr-defined]
        _m.__package__ = _name
        sys.modules[_name] = _m

_events_stub = ModuleType(f"{_PKG}.events")
_events_stub.cx_async = None  # type: ignore[attr-defined]
sys.modules[f"{_PKG}.events"] = _events_stub

_TP_DIR = (
    _REPO
    / "services/universal-stargate/systems/pipeline/core"
    / "handlers/thread_persistence"
)


def _load_module(name: str, relpath: str) -> ModuleType:
    path = _TP_DIR / relpath
    spec = importlib.util.spec_from_file_location(
        f"{_PKG}.{name}",
        path,
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG  # type: ignore[attr-defined]
    sys.modules[f"{_PKG}.{name}"] = mod
    spec.loader.exec_module(mod)  # type: ignore[arg-type]
    return mod


_ta = _load_module("turn_assertions", "turn_assertions.py")
_win = _load_module("window", "window.py")

# Public helpers under test
_is_consolidation_summary = _ta._is_consolidation_summary
extract_latest_summary = _ta.extract_latest_summary
is_turn_assertion = _ta.is_turn_assertion
turns_from_assertions = _ta.turns_from_assertions
build_referential_window = _win.build_referential_window


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _turn(pred: str, claim: str, aid: int, superseded_by: Any = None) -> dict:
    return {
        "id": aid,
        "predicate_form": pred,
        "claim": claim,
        "superseded_by": superseded_by,
        "evidence_uris": [],
    }


def _summary(
    turn_idx: int, aid: int, text: str = "Summary.", superseded_by: Any = None
) -> dict:
    return {
        "id": aid,
        "predicate_form": f"thread_summary({turn_idx})",
        "claim": f"archive summary: {text}",
        "superseded_by": superseded_by,
        "evidence_uris": [],
    }


# ---------------------------------------------------------------------------
# _is_consolidation_summary
# ---------------------------------------------------------------------------


class TestIsConsolidationSummary:
    def test_valid_summary(self):
        assert _is_consolidation_summary(_summary(4, 99)) is True

    def test_superseded_excluded(self):
        assert _is_consolidation_summary(_summary(4, 99, superseded_by=100)) is False

    def test_missing_claim_prefix(self):
        a = {
            "id": 1,
            "predicate_form": "thread_summary(4)",
            "claim": "bad claim",
            "superseded_by": None,
        }
        assert _is_consolidation_summary(a) is False

    def test_wrong_predicate(self):
        a = {
            "id": 1,
            "predicate_form": "user_turn(4)",
            "claim": "archive summary: x",
            "superseded_by": None,
        }
        assert _is_consolidation_summary(a) is False

    def test_turn_assertion_excluded(self):
        assert _is_consolidation_summary(_turn("user_turn(0)", "User: hi", 1)) is False


# ---------------------------------------------------------------------------
# extract_latest_summary
# ---------------------------------------------------------------------------


class TestExtractLatestSummary:
    def test_no_summaries(self):
        assertions = [
            _turn("user_turn(0)", "User: hi", 1),
            _turn("assistant_turn(0)", "Assistant: hello", 2),
        ]
        assert extract_latest_summary(assertions) is None

    def test_single_summary(self):
        assertions = [_summary(4, 99, "First summary.")]
        result = extract_latest_summary(assertions)
        assert result is not None
        assert result["id"] == 99

    def test_picks_highest_boundary(self):
        assertions = [
            _summary(4, 10, "Old."),
            _summary(8, 11, "Newer."),
            _summary(6, 12, "Middle."),
        ]
        result = extract_latest_summary(assertions)
        assert result is not None
        assert result["id"] == 11

    def test_superseded_summary_ignored(self):
        assertions = [
            _summary(8, 10, "Old.", superseded_by=99),
            _summary(4, 11, "Newer."),
        ]
        result = extract_latest_summary(assertions)
        assert result is not None
        assert result["id"] == 11

    def test_empty(self):
        assert extract_latest_summary([]) is None


# ---------------------------------------------------------------------------
# build_referential_window — summary prepend
# ---------------------------------------------------------------------------


def _make_assertions(n_turns: int, with_summary: bool = False) -> list[dict]:
    items: list[dict] = []
    aid = 1
    for i in range(n_turns):
        items.append(_turn(f"user_turn({i})", f"User: msg {i}", aid))
        aid += 1
        items.append(_turn(f"assistant_turn({i})", f"Assistant: reply {i}", aid))
        aid += 1
    if with_summary:
        items.append(_summary(n_turns - 2, aid, "Prior context compressed."))
    return items


class TestBuildReferentialWindow:
    def _run(self, anchor_id: str, assertions: list[dict], k: int) -> list[dict]:
        import asyncio

        async def _impl() -> list[dict]:
            with patch.object(_ta, "cx_async", new_callable=AsyncMock) as mock_cx:
                mock_cx.return_value = {"assertions": assertions}
                return await build_referential_window(anchor_id, k=k)

        return asyncio.run(_impl())

    def test_no_summary_returns_turns_only(self):
        assertions = _make_assertions(3, with_summary=False)
        result = self._run("anchor:x", assertions, k=8)
        assert all(m["role"] in {"user", "assistant"} for m in result)
        assert len(result) == 6  # 3 turns × 2 messages

    def test_with_summary_prepends_system_message(self):
        assertions = _make_assertions(5, with_summary=True)
        result = self._run("anchor:x", assertions, k=8)
        assert result[0]["role"] == "system"
        assert result[0]["content"].startswith("[Archive summary]\n")
        assert "Prior context compressed." in result[0]["content"]
        # Remaining messages are turn role messages
        assert all(m["role"] in {"user", "assistant"} for m in result[1:])

    def test_k_limits_turns_but_not_summary(self):
        assertions = _make_assertions(6, with_summary=True)
        result = self._run("anchor:x", assertions, k=4)
        assert result[0]["role"] == "system"
        assert len(result) == 5  # 1 summary + 4 tail turns

    def test_empty_anchor(self):
        result = self._run("anchor:empty", [], k=8)
        assert result == []

    def test_superseded_summary_not_prepended(self):
        assertions = _make_assertions(3)
        # Add superseded summary — should be ignored
        assertions.append(_summary(1, 999, "Old.", superseded_by=100))
        result = self._run("anchor:x", assertions, k=8)
        assert all(m["role"] != "system" for m in result)

    def test_regression_no_summary_unchanged(self):
        """Window without summary produces the same output as before Phase 2."""
        items = [
            _turn("user_turn(0)", "User: first", 1),
            _turn("assistant_turn(0)", "Assistant: second", 2),
        ]
        result = self._run("anchor:reg", items, k=8)
        assert result == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
        ]
