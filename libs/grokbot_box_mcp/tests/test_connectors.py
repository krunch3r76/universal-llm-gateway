"""Connector command string tests."""

from __future__ import annotations

import pytest
from grokbot_box_mcp.connectors import (
    connector_launcher_invocation,
    default_connector_command,
)
from grokbot_box_mcp.headers import box_launcher_path


@pytest.mark.offline
def test_default_connector_uses_sand_data_launcher() -> None:
    cmd = default_connector_command("code")
    assert cmd.startswith(box_launcher_path())
    assert "mcp.k-1.me/mcp/code/" in cmd


@pytest.mark.offline
def test_launcher_invocation_normalizes_url_trailing_slash() -> None:
    cmd = connector_launcher_invocation("https://mcp.k-1.me/mcp/life")
    assert cmd.endswith("https://mcp.k-1.me/mcp/life/")
