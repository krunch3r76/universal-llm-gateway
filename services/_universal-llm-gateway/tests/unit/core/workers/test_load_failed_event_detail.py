"""Gateway emit and WebSocket builder carry bounded process text."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

_repo_root = Path(__file__).resolve().parents[6]
_gateway_root = str(Path(__file__).resolve().parents[4])
_libs = str(_repo_root / "libs")
sys.path.insert(0, _libs)
sys.path.insert(0, _gateway_root)
for _name in list(sys.modules):
    if _name != "universal_protocol" and not _name.startswith("universal_protocol."):
        continue
    _mod_file = getattr(sys.modules[_name], "__file__", "") or ""
    if "ulg-arc-worktrees" not in _mod_file.replace("\\", "/"):
        del sys.modules[_name]

from src.core.errors import WorkerInitializationError  # noqa: E402
from src.core.events.types import MODEL_LOAD_FAILED  # noqa: E402
from src.core.websocket.event_forwarder.message_builders import (  # noqa: E402
    build_sync_message,
)
from src.core.workers.model_operations.load_flow.events import (  # noqa: E402
    emit_loading_event,
)
from src.core.workers.model_operations.load_flow.failure_handle import (  # noqa: E402
    load_failure_process_text,
)


def test_process_text_prefers_internal_error_and_drops_context() -> None:
    """M1 input: internal_error wins; context and stack traces stay off the hop."""
    secret = "SECRETTOKEN"
    exc = WorkerInitializationError(
        message="Failed to execute command 'load_model'",
        internal_error="ProcessError: llama exited 1",
        stack_trace=f"traceback {secret}",
        context={"token": secret, "command": {"api_key": secret}},
    )
    text = load_failure_process_text(exc)
    assert text == "ProcessError: llama exited 1"
    assert secret not in text
    assert "Failed to execute command" not in text


def test_process_text_falls_back_to_message_without_internal_error() -> None:
    exc = WorkerInitializationError(
        message="Failed to execute command 'load_model'",
        context={"token": "SECRETTOKEN"},
    )
    assert load_failure_process_text(exc) == "Failed to execute command 'load_model'"
    assert "SECRETTOKEN" not in load_failure_process_text(exc)


def test_ws_builder_carries_process_text_and_failure_reason() -> None:
    """M1: WebSocket builder output keeps both fields."""
    message = build_sync_message(
        MODEL_LOAD_FAILED,
        {
            "model_id": "qwen3-14b-q4-k-m-40960",
            "error_message": "ProcessError: llama exited 1",
            "failure_reason": "unknown",
        },
    )
    assert message is not None
    assert message.data["error_message"] == "ProcessError: llama exited 1"
    assert message.data["failure_reason"] == "unknown"


def test_ws_builder_omits_failure_reason_when_absent() -> None:
    message = build_sync_message(
        MODEL_LOAD_FAILED,
        {
            "model_id": "qwen3-14b-q4-k-m-40960",
            "error_message": "Failed to execute command 'load_model'",
        },
    )
    assert message is not None
    assert "failure_reason" not in message.data


def test_emit_bounds_classified_process_text() -> None:
    """M6: the gateway emit hop keeps at most 512 characters."""

    class _Bus:
        def __init__(self) -> None:
            self.events: list = []

        async def publish_nowait(self, event) -> None:
            self.events.append(event)

    class _Controller:
        def __init__(self) -> None:
            self.event_bus = _Bus()

    async def _run() -> None:
        controller = _Controller()
        await emit_loading_event(
            controller, "qwen3-14b-q4-k-m-40960", "failed", "x" * 600
        )
        payload = controller.event_bus.events[0].payload
        assert len(payload["error_message"]) == 512
        assert payload["error_message"] == "x" * 512
        assert payload["failure_reason"] == "unknown"

    with patch(
        "src.core.workers.model_operations.failure_snapshot.build_worker_snapshot",
        lambda _controller, _model_id: None,
    ):
        asyncio.run(_run())


def test_edge_gateway_startup_module_imports() -> None:
    """M7: edge gateway startup path imports (outage guard)."""
    from src.app.app_factory import create_app

    assert callable(create_app)
