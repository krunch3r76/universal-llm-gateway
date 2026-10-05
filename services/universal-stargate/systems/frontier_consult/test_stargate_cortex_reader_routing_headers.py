"""StargateCortexReader sends internal routing headers, not adapter claims."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from systems.frontier_consult.stargate_cortex_reader import StargateCortexReader

pytestmark = pytest.mark.offline


def test_stargate_cortex_reader_dispatch_headers() -> None:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"entity_id": "todo:x"}

    with patch(
        "systems.frontier_consult.stargate_cortex_reader.make_sync_client"
    ) as client_factory:
        client = client_factory.return_value.__enter__.return_value
        client.post.return_value = response
        StargateCortexReader().entity_get("todo:x")

    kwargs = client.post.call_args.kwargs
    assert kwargs["headers"]["X-ULG-Caller"] == "stargate_cortex_reader"
    assert "via_adapter" not in kwargs["json"]
