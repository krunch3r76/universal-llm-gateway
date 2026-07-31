"""Unit tests for thread_persistence/compaction_summarize pure helpers.

Tests collapse-set selection and idempotency guard in isolation.
Loaded via importlib with a minimal mock package chain so the
``from .events import cx_async`` relative import resolves cleanly
without triggering the full pipeline ``__init__`` chain.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

# ---------------------------------------------------------------------------
# Bootstrap — inject minimal mock package hierarchy so relative imports in
# compaction_summarize resolve. The only module-level package dep is
# ``from .events import cx_async``; we stub it with a no-op sentinel.
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

_artifact_stub = ModuleType(f"{_PKG}.artifact")
_artifact_stub.resolve_artifact_path = lambda _uri: None  # type: ignore[attr-defined]
sys.modules[f"{_PKG}.artifact"] = _artifact_stub

_TA_PATH = (
    _REPO
    / "services/universal-stargate/systems/pipeline/core"
    / "handlers/thread_persistence/turn_assertions.py"
)
_ta_spec = importlib.util.spec_from_file_location(
    f"{_PKG}.turn_assertions",
    _TA_PATH,
)
assert _ta_spec and _ta_spec.loader
_ta = importlib.util.module_from_spec(_ta_spec)
_ta.__package__ = _PKG  # type: ignore[attr-defined]
sys.modules[f"{_PKG}.turn_assertions"] = _ta
_ta_spec.loader.exec_module(_ta)  # type: ignore[arg-type]

_TC_PATH = (
    _REPO
    / "services/universal-stargate/systems/pipeline/core"
    / "handlers/thread_persistence/thread_compression.py"
)
_tc_spec = importlib.util.spec_from_file_location(
    f"{_PKG}.thread_compression",
    _TC_PATH,
)
assert _tc_spec and _tc_spec.loader
_tc = importlib.util.module_from_spec(_tc_spec)
_tc.__package__ = _PKG  # type: ignore[attr-defined]
sys.modules[f"{_PKG}.thread_compression"] = _tc
_tc_spec.loader.exec_module(_tc)  # type: ignore[arg-type]

_CS_PATH = (
    _REPO
    / "services/universal-stargate/systems/pipeline/core"
    / "handlers/thread_persistence/compaction_summarize.py"
)
_spec = importlib.util.spec_from_file_location(
    f"{_PKG}.compaction_summarize",
    _CS_PATH,
)
assert _spec and _spec.loader
_cs = importlib.util.module_from_spec(_spec)
_cs.__package__ = _PKG  # type: ignore[attr-defined]
sys.modules[f"{_PKG}.compaction_summarize"] = _cs
_spec.loader.exec_module(_cs)  # type: ignore[arg-type]

is_already_summarized = _cs.is_already_summarized
is_thread_summary_assertion = _cs.is_thread_summary_assertion
select_collapse_set = _cs.select_collapse_set
build_summary_input = _cs.build_summary_input


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _turn(pred: str, claim: str, aid: int, superseded_by=None) -> dict:
    return {
        "id": aid,
        "predicate_form": pred,
        "claim": claim,
        "superseded_by": superseded_by,
        "evidence_uris": [f"workspaces://ulg/.runtime/t/turn_{aid:04d}.json"],
    }


def _summary(turn_idx: int, aid: int, superseded_by=None) -> dict:
    return {
        "id": aid,
        "predicate_form": f"thread_summary({turn_idx})",
        "claim": f"archive summary: Summary up to turn {turn_idx}.",
        "superseded_by": superseded_by,
        "evidence_uris": [],
    }


# ---------------------------------------------------------------------------
# is_thread_summary_assertion
# ---------------------------------------------------------------------------


class TestIsThreadSummaryAssertion:
    def test_detects_summary_predicate(self):
        assert is_thread_summary_assertion(_summary(5, 99)) is True

    def test_ignores_superseded(self):
        assert is_thread_summary_assertion(_summary(5, 99, superseded_by=100)) is False

    def test_ignores_user_turn(self):
        a = _turn("user_turn(3)", "User: hi", 1)
        assert is_thread_summary_assertion(a) is False

    def test_ignores_assistant_turn(self):
        a = _turn("assistant_turn(3)", "Assistant: hi", 2)
        assert is_thread_summary_assertion(a) is False


# ---------------------------------------------------------------------------
# is_already_summarized
# ---------------------------------------------------------------------------


class TestIsAlreadySummarized:
    def test_no_summaries(self):
        assertions = [
            _turn("user_turn(0)", "User: hi", 1),
            _turn("assistant_turn(0)", "Assistant: hello", 2),
        ]
        assert is_already_summarized(assertions, collapse_up_to=1) is False

    def test_summary_exactly_at_boundary(self):
        # N=3 >= collapse_up_to=3 → already covered
        assert is_already_summarized([_summary(3, 99)], collapse_up_to=3) is True

    def test_summary_above_boundary(self):
        assert is_already_summarized([_summary(5, 99)], collapse_up_to=3) is True

    def test_summary_below_boundary(self):
        # N=2 < collapse_up_to=3 → NOT covered
        assert is_already_summarized([_summary(2, 99)], collapse_up_to=3) is False

    def test_superseded_summary_ignored(self):
        assert (
            is_already_summarized(
                [_summary(5, 99, superseded_by=100)], collapse_up_to=3
            )
            is False
        )

    def test_one_of_multiple_covers(self):
        assertions = [_summary(2, 10), _summary(7, 11)]
        assert is_already_summarized(assertions, collapse_up_to=5) is True

    def test_empty(self):
        assert is_already_summarized([], collapse_up_to=5) is False


# ---------------------------------------------------------------------------
# select_collapse_set
# ---------------------------------------------------------------------------


class TestSelectCollapseSet:
    def _thread(self, n: int) -> list[dict]:
        items = []
        aid = 1
        for i in range(n):
            items.append(_turn(f"user_turn({i})", f"User: msg {i}", aid))
            aid += 1
            items.append(_turn(f"assistant_turn({i})", f"Assistant: reply {i}", aid))
            aid += 1
        return items

    def test_selects_below_boundary(self):
        assertions = self._thread(5)
        result = select_collapse_set(assertions, collapse_up_to=3)
        preds = {a["predicate_form"] for a in result}
        for i in range(3):
            assert f"user_turn({i})" in preds
            assert f"assistant_turn({i})" in preds
        assert "user_turn(3)" not in preds
        assert "user_turn(4)" not in preds
        assert len(result) == 6

    def test_superseded_excluded(self):
        assertions = [
            _turn("user_turn(0)", "User: hi", 1, superseded_by=99),
            _turn("assistant_turn(0)", "Assistant: hello", 2),
        ]
        result = select_collapse_set(assertions, collapse_up_to=3)
        preds = {a["predicate_form"] for a in result}
        assert "user_turn(0)" not in preds
        assert "assistant_turn(0)" in preds

    def test_thread_summary_excluded(self):
        assertions = [_turn("user_turn(0)", "User: hi", 1), _summary(0, 99)]
        result = select_collapse_set(assertions, collapse_up_to=3)
        preds = {a["predicate_form"] for a in result}
        assert "thread_summary(0)" not in preds
        assert "user_turn(0)" in preds

    def test_zero_boundary_empty(self):
        assert select_collapse_set(self._thread(3), collapse_up_to=0) == []

    def test_empty_input(self):
        assert select_collapse_set([], collapse_up_to=5) == []

    def test_all_below_boundary(self):
        result = select_collapse_set(self._thread(3), collapse_up_to=10)
        assert len(result) == 6


# ---------------------------------------------------------------------------
# build_summary_input
# ---------------------------------------------------------------------------


class TestBuildSummaryInput:
    def test_role_order(self):
        collapse_set = [
            _turn("user_turn(0)", "User: hello", 1),
            _turn("assistant_turn(0)", "Assistant: world", 2),
        ]
        lines = build_summary_input(collapse_set).splitlines()
        assert lines[0] == "User: hello"
        assert lines[1] == "Assistant: world"

    def test_sorted_by_turn_index(self):
        collapse_set = [
            _turn("user_turn(1)", "User: second", 3),
            _turn("user_turn(0)", "User: first", 1),
            _turn("assistant_turn(0)", "Assistant: first reply", 2),
            _turn("assistant_turn(1)", "Assistant: second reply", 4),
        ]
        lines = build_summary_input(collapse_set).splitlines()
        assert lines[0] == "User: first"
        assert lines[1] == "Assistant: first reply"
        assert lines[2] == "User: second"
        assert lines[3] == "Assistant: second reply"

    def test_strips_user_prefix(self):
        cs = [_turn("user_turn(0)", "User: stripped", 1)]
        assert build_summary_input(cs) == "User: stripped"

    def test_empty(self):
        assert build_summary_input([]) == ""

    # ------------------------------------------------------------------
    # artifact digest augmentation (Stage A)
    # ------------------------------------------------------------------

    def test_artifacts_none_is_baseline(self):
        """artifacts=None → identical to calling without artifacts."""
        cs = [
            _turn("user_turn(0)", "User: hello", 1),
            _turn("assistant_turn(0)", "Assistant: world", 2),
        ]
        assert build_summary_input(cs, artifacts=None) == build_summary_input(cs)

    def test_artifacts_appends_tool_activity(self):
        """Assistant turn with tool_calls in sidecar → digest appended."""
        cs = [
            _turn("assistant_turn(0)", "Assistant: used a tool", 2),
        ]
        artifacts = {
            2: {
                "tool_calls": [
                    {"name": "cortex", "ok": True},
                    {"name": "search", "ok": False},
                ]
            }
        }
        output = build_summary_input(cs, artifacts=artifacts)
        lines = output.splitlines()
        assert lines[0] == "Assistant: used a tool"
        assert "[Tool activity:" in lines[1]
        assert "cortex(ok)" in lines[1]
        assert "search(fail)" in lines[1]

    def test_artifacts_no_tool_calls_no_extra_line(self):
        """Artifact present but empty tool_calls → no digest line added."""
        cs = [_turn("assistant_turn(0)", "Assistant: text only", 2)]
        artifacts = {2: {"tool_calls": []}}
        output = build_summary_input(cs, artifacts=artifacts)
        assert output == "Assistant: text only"

    def test_artifacts_user_turn_no_digest(self):
        """User turns never get a tool activity digest even if artifact present."""
        cs = [_turn("user_turn(0)", "User: hi", 1)]
        artifacts = {1: {"tool_calls": [{"name": "cortex", "ok": True}]}}
        output = build_summary_input(cs, artifacts=artifacts)
        assert output == "User: hi"
        assert "[Tool activity:" not in output

    def test_artifacts_missing_assertion_id_skipped(self):
        """Assertion not in artifacts dict → no digest appended."""
        cs = [_turn("assistant_turn(0)", "Assistant: alone", 99)]
        artifacts = {1: {"tool_calls": [{"name": "x", "ok": True}]}}
        assert build_summary_input(cs, artifacts=artifacts) == "Assistant: alone"
