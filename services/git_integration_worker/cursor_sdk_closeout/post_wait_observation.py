"""Post-wait conversation/artifact reads, tool-call counting, stream-vs-conversation deviation.

``read_post_wait_snapshot`` is the authority read after SDK wait, with a bounded
poll when status is finished and conversation is empty. ``import time`` stays
inside the poll branch. Poll constants stay in this module.
``stream_only_effect_deviations`` names the friction-21654 stream-only gap.
"""

from __future__ import annotations

from typing import Any

from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)

from .closeout_records import PostWaitSnapshot
from .sdk_git_snapshot import extract_sdk_git_snapshot

_POST_WAIT_POLL_ATTEMPTS = 3
_POST_WAIT_POLL_INTERVAL_S = 0.2

def count_tool_calls(turns: list) -> int:
    total = 0
    for turn in turns:
        steps = getattr(getattr(turn, "turn", None), "steps", ()) or ()
        total += sum(1 for step in steps if getattr(step, "type", "") == "toolCall")
    return total


def _post_wait_needs_poll(*, conversation: list[Any], status: str) -> bool:
    if status == "finished" and not conversation:
        return True
    return False


def read_post_wait_snapshot(
    *,
    run: Any,
    agent: Any,
    result: Any,
    poll_fallback: bool = True,
) -> PostWaitSnapshot:
    """Post-wait authority reads; bounded poll when immediate snapshot is incomplete."""

    def _read() -> tuple[list[Any], tuple[str, ...], dict[str, Any] | None]:
        turns = run.conversation()
        artifact_paths: list[str] = []
        list_artifacts_fn = getattr(agent, "list_artifacts", None)
        if callable(list_artifacts_fn):
            try:
                raw_artifacts = list_artifacts_fn()
                if raw_artifacts:
                    artifact_paths = [str(path) for path in raw_artifacts if path]
            except Exception:  # noqa: BLE001
                artifact_paths = []
        sdk_git = extract_sdk_git_snapshot(getattr(result, "git", None))
        return turns, tuple(artifact_paths), sdk_git

    status = str(getattr(result, "status", ""))
    conversation, artifact_paths, sdk_git = _read()
    if poll_fallback and _post_wait_needs_poll(
        conversation=conversation, status=status
    ):
        import time

        for _ in range(_POST_WAIT_POLL_ATTEMPTS):
            time.sleep(_POST_WAIT_POLL_INTERVAL_S)
            conversation, artifact_paths, sdk_git = _read()
            if not _post_wait_needs_poll(conversation=conversation, status=status):
                break
    return PostWaitSnapshot(
        conversation=conversation,
        artifact_paths=artifact_paths,
        sdk_git=sdk_git,
    )


def stream_only_effect_deviations(
    *,
    stream_tool_calls: tuple[ToolCallObservation, ...],
    conversation_tool_call_count: int,
) -> tuple[str, ...]:
    if stream_tool_calls and conversation_tool_call_count < len(stream_tool_calls):
        return ("stream_only_effect",)
    return ()
