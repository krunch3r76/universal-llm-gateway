"""Regression: cortex brief view materialization distinguishes fetch failure from empty graph."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools._boot_helpers._briefing_card_render import render_views_section
from tools._boot_helpers._manifest import build_manifest
from tools.cortex_named_tools import _boot_runner as boot_runner

_SECRET = "SECRET_LEAK_MARKER_XYZ"
_VIEW_A = "plan:view-a"
_VIEW_B = "plan:view-b"
_VIEW_C = "plan:view-c"


def _manifest_views(materialized: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    manifest = build_manifest(
        plan_phases=None,
        in_flight_todos=None,
        todo_total=0,
        unread_count=0,
        reflective_total=0,
        recent_mentions=None,
        skills=None,
        views_data=materialized,
    )
    return {
        row["section"]: row for row in manifest if row["section"].startswith("views/")
    }


def _card_views_text(materialized: list[dict[str, Any]]) -> str:
    return "\n".join(render_views_section(materialized))


def test_materialize_valid_nonzero_and_empty_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def cx(method: str, path: str, **_kw: Any) -> dict[str, Any]:
        assert method == "GET"
        calls.append(path)
        if "view-a" in path:
            return {"entity_count": 3, "edge_count": 2}
        if "view-b" in path:
            return {"entity_count": 0, "edge_count": 0}
        raise AssertionError(path)

    monkeypatch.setattr("tools._cortex_relay.cx", cx)
    rows = boot_runner._materialize_views([_VIEW_A, _VIEW_B])
    assert len(calls) == 2
    assert rows[0]["fetch_status"] == "ok" and rows[0]["entity_count"] == 3
    assert rows[1]["fetch_status"] == "ok" and rows[1]["entity_count"] == 0
    assert rows[1]["edge_count"] == 0
    assert "fetch_error" not in rows[0]


def test_materialize_dict_error_and_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def cx(method: str, path: str, **_kw: Any) -> dict[str, Any]:
        if "view-a" in path:
            return {"error": f"upstream {_SECRET}"}
        raise RuntimeError(_SECRET)

    monkeypatch.setattr("tools._cortex_relay.cx", cx)
    rows = boot_runner._materialize_views([_VIEW_A, _VIEW_B])
    assert rows[0]["fetch_status"] == "failed"
    assert rows[0]["fetch_error"] == "fetch_failed"
    assert rows[0]["entity_count"] is None
    assert rows[1]["fetch_error"] == "fetch_failed"


@pytest.mark.parametrize(
    "payload",
    [
        "not-a-dict",
        {"entity_count": 1},
        {"entity_count": None, "edge_count": 0},
        {"entity_count": True, "edge_count": 0},
        {"entity_count": -1, "edge_count": 0},
        {"entity_count": 1.5, "edge_count": 0},
        {"entity_count": "01a", "edge_count": 0},
        {"entity_count": "5", "edge_count": "bad"},
    ],
)
def test_materialize_invalid_response_shapes(
    monkeypatch: pytest.MonkeyPatch, payload: Any
) -> None:
    def cx(_method: str, _path: str, **_kw: Any) -> Any:
        return payload

    monkeypatch.setattr("tools._cortex_relay.cx", cx)
    row = boot_runner._materialize_views([_VIEW_A])[0]
    assert row["fetch_status"] == "failed"
    assert row["fetch_error"] == "invalid_response"
    assert row["entity_count"] is None


@pytest.mark.parametrize(
    "entity_count",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        1.0,
        "²",
    ],
)
def test_exotic_entity_counts_invalid_without_aborting_sibling(
    monkeypatch: pytest.MonkeyPatch, entity_count: Any
) -> None:
    def cx(_method: str, path: str, **_kw: Any) -> dict[str, Any]:
        if "view-a" in path:
            return {"entity_count": entity_count, "edge_count": 0}
        return {"entity_count": 0, "edge_count": 0}

    monkeypatch.setattr("tools._cortex_relay.cx", cx)
    rows = boot_runner._materialize_views([_VIEW_A, _VIEW_B])
    assert rows[0]["fetch_status"] == "failed"
    assert rows[0]["fetch_error"] == "invalid_response"
    assert rows[1]["fetch_status"] == "ok"


def test_overlong_ascii_digit_string_invalid_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(sys, "set_int_max_str_digits"):
        pytest.skip("set_int_max_str_digits unavailable")
    prior = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(641)
        overlong = "9" * 700

        def cx(_method: str, path: str, **_kw: Any) -> dict[str, Any]:
            if "view-a" in path:
                return {"entity_count": overlong, "edge_count": 0}
            return {"entity_count": 1, "edge_count": 0}

        monkeypatch.setattr("tools._cortex_relay.cx", cx)
        rows = boot_runner._materialize_views([_VIEW_A, _VIEW_B])
    finally:
        sys.set_int_max_str_digits(prior)

    assert rows[0]["fetch_status"] == "failed"
    assert rows[0]["fetch_error"] == "invalid_response"
    assert rows[1]["fetch_status"] == "ok"
    assert rows[1]["entity_count"] == 1


def test_subgraph_count_value_rejects_non_integer_floats() -> None:
    assert boot_runner._subgraph_count_value(1.0) is None
    assert boot_runner._subgraph_count_value(math.nan) is None
    assert boot_runner._subgraph_count_value(math.inf) is None


def test_digit_string_counts_normalize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tools._cortex_relay.cx",
        lambda *_a, **_k: {"entity_count": "12", "edge_count": "0"},
    )
    row = boot_runner._materialize_views([_VIEW_A])[0]
    assert row["fetch_status"] == "ok"
    assert row["entity_count"] == 12
    assert row["edge_count"] == 0


def test_mixed_views_isolate_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def cx(_method: str, path: str, **_kw: Any) -> dict[str, Any]:
        if "view-a" in path:
            return {"entity_count": 2, "edge_count": 1}
        if "view-b" in path:
            return {"error": "gone"}
        return {"entity_count": 0, "edge_count": 0}

    monkeypatch.setattr("tools._cortex_relay.cx", cx)
    rows = boot_runner._materialize_views([_VIEW_A, _VIEW_B, _VIEW_C])
    assert [r["fetch_status"] for r in rows] == ["ok", "failed", "ok"]


def test_card_and_manifest_consistency_no_secret_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def cx(_method: str, path: str, **_kw: Any) -> dict[str, Any]:
        if "view-b" in path:
            return {"error": f"detail {_SECRET}"}
        return {"entity_count": 0, "edge_count": 0}

    monkeypatch.setattr("tools._cortex_relay.cx", cx)
    materialized = boot_runner._materialize_views([_VIEW_A, _VIEW_B])
    card = _card_views_text(materialized)
    sections = _manifest_views(materialized)

    assert "0 entities, 0 edges" in card
    assert "unavailable (fetch_failed)" in card
    assert _SECRET not in card

    ok_sec = sections[f"views/{_VIEW_A}"]
    fail_sec = sections[f"views/{_VIEW_B}"]
    assert ok_sec["fetch_status"] == "ok" and ok_sec["entity_count"] == 0
    assert fail_sec["fetch_status"] == "failed"
    assert fail_sec["entity_count"] is None
    assert _SECRET not in str(fail_sec)


def test_materialize_does_not_invoke_disk_or_bus_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_mock = MagicMock()
    record_mock = MagicMock()
    monkeypatch.setattr(
        "tools._cortex_relay.cx", lambda *_a, **_k: {"entity_count": 0, "edge_count": 0}
    )
    monkeypatch.setattr(boot_runner, "durable_write_text", write_mock)
    monkeypatch.setattr(boot_runner, "record", record_mock)

    boot_runner._materialize_views([_VIEW_A])
    write_mock.assert_not_called()
    record_mock.assert_not_called()


def test_legacy_row_without_fetch_status_renders_counts() -> None:
    legacy = [
        {
            "entity_id": _VIEW_A,
            "entity_count": 4,
            "edge_count": 1,
            "retrieval_hint": "hint",
        }
    ]
    card = _card_views_text(legacy)
    assert "4 entities, 1 edges" in card
    sec = _manifest_views(legacy)[f"views/{_VIEW_A}"]
    assert sec["entity_count"] == 4
    assert "fetch_status" not in sec
