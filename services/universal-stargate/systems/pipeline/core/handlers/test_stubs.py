"""Disposable Phase A test stubs for the persistent-chat pipeline.

These handlers exist ONLY to anchor end-to-end tests for Phase 1-2 of the
``cortex-chat-openai`` rollout: they emulate the eventual ``assemble_thread``,
``archive_user_turn``, and ``archive_assistant_turn`` handlers using a
process-local in-memory anchor store keyed by ``context.chat_id``.

Registration is import-side-effect — tests must ``import`` this module
explicitly to register the stubs with ``HandlerRegistry``.

Once the real handlers land in Phase 4, this file (and the test fixture
directory it accompanies) is deleted — there is no migration shim.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .protocol import StepOutput
from .registry import register_handler

if TYPE_CHECKING:
    from ..schemas import StepConfig
    from .protocol import PipelineContext


# Process-local anchor store keyed by ``chat_id``. Disposable; not persisted.
_MEM_ANCHOR_STORE: dict[str, dict] = {}


def _anchor_for(chat_id: str | None) -> dict:
    """Return the in-memory anchor for ``chat_id``, creating it on first touch."""
    key = chat_id or "default"
    return _MEM_ANCHOR_STORE.setdefault(key, {"turn_count": 0, "history": []})


def _artifact_uri(chat_id: str | None, turn_index: int) -> str:
    """Synthesize an artifact URI for an emulated archive write."""
    key = chat_id or "default"
    return (
        f"workspaces://universal-llm-gateway/.runtime/thread-artifacts/"
        f"{key}/turn_{turn_index:04d}.json"
    )


@register_handler
class AssembleStubHandler:
    """Emulates ``assemble_thread``: emits the anchor's replayed history."""

    step_type = "assemble_stub_v1"

    async def execute(
        self, step: StepConfig, context: PipelineContext
    ) -> StepOutput:
        anchor = _anchor_for(context.chat_id)
        payload = {
            "messages": list(anchor["history"]),
            "anchor_id": f"anchor:{context.chat_id or 'default'}",
            "turn_index": anchor["turn_count"],
        }
        return StepOutput(raw=json.dumps(payload), json=payload)


@register_handler
class ArchiveUserStubHandler:
    """Emulates ``archive_user_turn``: appends user text to the anchor."""

    step_type = "archive_user_stub_v1"

    async def execute(
        self, step: StepConfig, context: PipelineContext
    ) -> StepOutput:
        anchor = _anchor_for(context.chat_id)
        turn_index = anchor["turn_count"]
        anchor["history"].append({"role": "user", "content": context.source_text})
        payload = {"artifact_uri": _artifact_uri(context.chat_id, turn_index)}
        return StepOutput(raw=json.dumps(payload), json=payload)


@register_handler
class ArchiveAssistantStubHandler:
    """Emulates ``archive_assistant_turn``: appends assistant text + bumps turn."""

    step_type = "archive_assistant_stub_v1"

    async def execute(
        self, step: StepConfig, context: PipelineContext
    ) -> StepOutput:
        anchor = _anchor_for(context.chat_id)
        turn_index = anchor["turn_count"]
        respond_output = context.outputs.get("respond")
        assistant_text = respond_output.raw if respond_output else "stub response"
        anchor["history"].append({"role": "assistant", "content": assistant_text})
        anchor["turn_count"] += 1
        payload = {"artifact_uri": _artifact_uri(context.chat_id, turn_index)}
        return StepOutput(raw=json.dumps(payload), json=payload)
