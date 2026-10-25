"""Control tower houses projection from the observability bridge."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from cortex_store.observability_bridge import query_agent_bus_houses
from cortex_store.routes.boot.control_tower import _aggregate


def test_query_agent_bus_houses_makes_exactly_one_bus_get(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("AGENT_BUS_TOKEN", "tok")
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"houses": []}
    client.get.return_value = resp
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    with patch("cortex_store.observability_bridge.make_sync_client", return_value=client):
        assert query_agent_bus_houses() == []
    client.get.assert_called_once()


def test_query_agent_bus_houses_returns_empty_without_token_or_bus(
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.delenv("AGENT_BUS_TOKEN", raising=False)
    assert query_agent_bus_houses() == []


def test_aggregate_includes_houses_key() -> None:
    conn = MagicMock()
    def _fake_query(_conn, sql, *args, **kwargs):  # noqa: ANN001, ARG001
        if "COUNT(*)" in sql:
            return [{"n": 0}]
        return []

    with patch("cortex_store.routes.boot.control_tower.db_query", side_effect=_fake_query):
        with patch(
            "cortex_store.routes.boot.control_tower._list_deadlines_impl",
            return_value={"items": []},
        ):
            with patch(
                "cortex_store.routes.boot.control_tower.query_agent_bus_threads",
                return_value=[],
            ):
                with patch(
                    "cortex_store.routes.boot.control_tower.query_agent_bus_houses",
                    return_value=[
                        {
                            "root_id": "20468",
                            "slug": "grok-sdk-fast-off",
                            "mission": "ruling:x",
                            "action_why": "hop",
                            "as_of": "2026-10-25T00:00:00Z",
                        }
                    ],
                ):
                    state = _aggregate(conn)
    assert "houses" in state
    assert state["houses"][0]["root_id"] == "20468"


def test_aggregate_legacy_sqlite_naive_as_of_does_not_raise() -> None:
    conn = MagicMock()

    def _fake_query(_conn, sql, *args, **kwargs):  # noqa: ANN001, ARG001
        if "COUNT(*)" in sql:
            return [{"n": 0}]
        return []

    legacy = "2026-10-25 08:00:00"
    with patch("cortex_store.routes.boot.control_tower.db_query", side_effect=_fake_query):
        with patch(
            "cortex_store.routes.boot.control_tower._list_deadlines_impl",
            return_value={"items": []},
        ):
            with patch(
                "cortex_store.routes.boot.control_tower.query_agent_bus_threads",
                return_value=[],
            ):
                with patch(
                    "cortex_store.routes.boot.control_tower.query_agent_bus_houses",
                    return_value=[
                        {
                            "root_id": "20468",
                            "slug": "legacy-ts",
                            "mission": "",
                            "action_why": "hop",
                            "as_of": legacy,
                        }
                    ],
                ):
                    state = _aggregate(conn)
    assert state["houses"][0]["as_of"] == legacy
    assert state["houses"][0]["age"] >= 0
