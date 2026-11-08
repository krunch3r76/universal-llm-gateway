"""Work-registry holder paint helpers."""

from __future__ import annotations

from scripts.model_manager.ui.dispatch_monitor.core.board_lines import (
    registry_holder_token,
    work_registry_line,
)
from scripts.model_manager.ui.dispatch_monitor.core.dtos import WorkRegistryRow


def test_work_registry_line_formats_holder_and_work_key() -> None:
    row = WorkRegistryRow(
        row_id="220e64d1-6a31-427f-b14c-cf91072b892a",
        kind="dispatch",
        status="running",
        work_key="todo:agent-sdk-instance-hub",
        work_key_display="todo:agent-sdk-instance-hub",
        thread_id="22573",
        caller_agent="cursor-glass-22555",
        seat_name="cursor-sdk",
    )
    text = work_registry_line(row)
    assert "todo:agent-sdk-instance-hub" in text
    assert "holder=cursor-sdk" in text
    assert "22573" in text


def test_registry_holder_token_on_sdk_dispatch_id() -> None:
    row = WorkRegistryRow(
        row_id="abc-dispatch-id",
        work_key="friction:1",
        work_key_display="friction:1",
        seat_name="glass seat",
    )
    tok = registry_holder_token("abc-dispatch-id", (row,))
    assert tok is not None
    assert "holder=glass seat" in tok
    assert "wk=friction:1" in tok
