"""Observability sql member limit parsing."""

from __future__ import annotations

from unittest.mock import patch

from tools.events import _query_event_service


def test_query_event_service_rejects_non_int_limit() -> None:
    with patch("event_store.query_client.query_sql") as mock_sql:
        result = _query_event_service(
            "sql",
            params={"sql": "SELECT 1", "limit": "abc"},
        )
    assert result == {"error": "limit must be an integer"}
    mock_sql.assert_not_called()
