"""Unit tests for thread-persistence turn_assertions helpers (F3).

Loaded via importlib with a minimal package stub so ``from .events import
cx_async`` resolves without importing ``systems.pipeline`` (DAGExecutor chain).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

_STARGATE_PATH = Path(__file__).resolve().parents[1] / "services" / "universal-stargate"
_HANDLERS_DIR = _STARGATE_PATH / "systems/pipeline/core/handlers"
_TP_DIR = _HANDLERS_DIR / "thread_persistence"
_PKG = "handlers.thread_persistence"


def _load_turn_assertions_module():
    """Register stub package and load turn_assertions without pipeline __init__."""
    if str(_STARGATE_PATH) not in sys.path:
        sys.path.insert(0, str(_STARGATE_PATH))

    if "handlers" not in sys.modules:
        handlers_pkg = types.ModuleType("handlers")
        handlers_pkg.__path__ = [str(_HANDLERS_DIR)]
        sys.modules["handlers"] = handlers_pkg

    if _PKG not in sys.modules:
        tp_pkg = types.ModuleType(_PKG)
        tp_pkg.__path__ = [str(_TP_DIR)]
        sys.modules[_PKG] = tp_pkg

    events_name = f"{_PKG}.events"
    if events_name not in sys.modules:
        events_spec = importlib.util.spec_from_file_location(
            events_name,
            _TP_DIR / "events.py",
        )
        events_mod = importlib.util.module_from_spec(events_spec)
        assert events_spec.loader is not None
        sys.modules[events_name] = events_mod
        events_spec.loader.exec_module(events_mod)

    ta_name = f"{_PKG}.turn_assertions"
    if ta_name in sys.modules:
        return sys.modules[ta_name]

    ta_spec = importlib.util.spec_from_file_location(
        ta_name,
        _TP_DIR / "turn_assertions.py",
    )
    ta_mod = importlib.util.module_from_spec(ta_spec)
    assert ta_spec.loader is not None
    sys.modules[ta_name] = ta_mod
    ta_spec.loader.exec_module(ta_mod)
    return ta_mod


_ta = _load_turn_assertions_module()
is_turn_assertion = _ta.is_turn_assertion
parse_turn_index = _ta.parse_turn_index
next_turn_index = _ta.next_turn_index
turns_from_assertions = _ta.turns_from_assertions
load_turn_assertions = _ta.load_turn_assertions
extract_latest_summary = _ta.extract_latest_summary
load_all_assertions = _ta.load_all_assertions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _summary_assertion(
    turn_boundary: int,
    aid: int,
    *,
    superseded_by=None,
) -> dict:
    return {
        "id": aid,
        "predicate_form": f"thread_summary({turn_boundary})",
        "claim": f"archive summary: Summarized up to turn {turn_boundary}.",
        "superseded_by": superseded_by,
        "evidence_uris": [],
    }


def test_is_turn_assertion_filters_superseded_and_non_turn() -> None:
    assert not is_turn_assertion(
        {"predicate_form": "user_turn(0)", "superseded_by": "x"}
    )
    assert not is_turn_assertion({"predicate_form": "has_status(open)"})
    assert is_turn_assertion({"predicate_form": "user_turn(1)", "claim": "user: hi"})
    assert is_turn_assertion(
        {"predicate_form": "assistant_turn(1)", "claim": "assistant: ok"}
    )


def test_parse_turn_index() -> None:
    assert parse_turn_index("user_turn(3)") == 3
    assert parse_turn_index("assistant_turn(0)") == 0
    assert parse_turn_index("has_status(open)") is None
    assert parse_turn_index("user_turn(bad)") is None


def test_next_turn_index_empty_and_max() -> None:
    assert next_turn_index([]) == 0
    assertions = [
        {"predicate_form": "user_turn(0)", "claim": "user: a"},
        {"predicate_form": "assistant_turn(2)", "claim": "assistant: b"},
        {"predicate_form": "has_status(open)", "claim": "ignored"},
    ]
    assert next_turn_index(assertions) == 3


def test_turns_from_assertions_sort_and_strip_role_prefix() -> None:
    assertions = [
        {"predicate_form": "assistant_turn(0)", "claim": "assistant: reply"},
        {"predicate_form": "user_turn(0)", "claim": "user: question"},
        {"predicate_form": "user_turn(1)", "claim": "user: follow-up"},
    ]
    turns = turns_from_assertions(assertions)
    assert turns == [
        (0, "user", "question"),
        (0, "assistant", "reply"),
        (1, "user", "follow-up"),
    ]


@pytest.mark.asyncio
async def test_load_turn_assertions_404_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _mock_cx_async(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert tool == "entity_get"
        return {"status_code": 404}

    monkeypatch.setattr(_ta, "cx_async", _mock_cx_async)
    assert await load_turn_assertions("thread:openai-chat:missing") == []


@pytest.mark.asyncio
async def test_load_turn_assertions_filters_turn_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _mock_cx_async(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "assertions": [
                {"predicate_form": "user_turn(0)", "claim": "user: hi"},
                {"predicate_form": "has_status(open)", "claim": "status"},
                {"predicate_form": "assistant_turn(0)", "claim": "assistant: yo"},
            ],
        }

    monkeypatch.setattr(_ta, "cx_async", _mock_cx_async)
    rows = await load_turn_assertions("thread:dispatch:abc")
    assert len(rows) == 2
    assert all(is_turn_assertion(r) for r in rows)


@pytest.mark.asyncio
async def test_load_turn_assertions_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _mock_cx_async(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"error": "upstream failure"}

    monkeypatch.setattr(_ta, "cx_async", _mock_cx_async)
    with pytest.raises(RuntimeError, match="failed to load anchor"):
        await load_turn_assertions("thread:openai-chat:bad")


# ---------------------------------------------------------------------------
# Phase 2 helpers — extract_latest_summary
# ---------------------------------------------------------------------------


class TestExtractLatestSummary:
    def test_none_when_no_summaries(self):
        assertions = [
            {"predicate_form": "user_turn(0)", "claim": "User: hi"},
            {"predicate_form": "assistant_turn(0)", "claim": "Assistant: yo"},
        ]
        assert extract_latest_summary(assertions) is None

    def test_returns_summary_assertion(self):
        assertions = [_summary_assertion(4, 99)]
        result = extract_latest_summary(assertions)
        assert result is not None
        assert result["predicate_form"] == "thread_summary(4)"

    def test_returns_highest_boundary(self):
        assertions = [
            _summary_assertion(2, 10),
            _summary_assertion(5, 11),
            _summary_assertion(3, 12),
        ]
        result = extract_latest_summary(assertions)
        assert result is not None
        assert result["predicate_form"] == "thread_summary(5)"

    def test_ignores_superseded(self):
        assertions = [
            _summary_assertion(5, 10, superseded_by=99),
            _summary_assertion(2, 11),
        ]
        result = extract_latest_summary(assertions)
        assert result is not None
        assert result["predicate_form"] == "thread_summary(2)"

    def test_ignores_missing_claim_prefix(self):
        # Malformed: predicate matches but claim missing the §6.10 prefix
        bad = {
            "id": 88,
            "predicate_form": "thread_summary(9)",
            "claim": "bad claim without prefix",
            "superseded_by": None,
            "evidence_uris": [],
        }
        assert extract_latest_summary([bad]) is None

    def test_empty_list(self):
        assert extract_latest_summary([]) is None


# ---------------------------------------------------------------------------
# Phase 2 helpers — load_all_assertions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_all_assertions_returns_full_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _mock(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "assertions": [
                {"predicate_form": "user_turn(0)", "claim": "User: hi"},
                {"predicate_form": "thread_summary(0)", "claim": "archive summary: x"},
                {
                    "predicate_form": "user_turn(1)",
                    "claim": "User: bye",
                    "superseded_by": 9,
                },
            ]
        }

    monkeypatch.setattr(_ta, "cx_async", _mock)
    rows = await load_all_assertions("thread:openai-chat:abc")
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_load_all_assertions_404_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _mock(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"status_code": 404}

    monkeypatch.setattr(_ta, "cx_async", _mock)
    assert await load_all_assertions("thread:openai-chat:missing") == []
