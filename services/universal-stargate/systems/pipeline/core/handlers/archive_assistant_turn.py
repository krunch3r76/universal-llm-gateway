"""``archive_assistant_turn_v1`` — persist the assistant turn.

Runs only on respond-success (the executor gates this step on the
generate step's completion). Writes the assistant turn's JSON artifact
to ``workspaces://.runtime/thread-artifacts/`` and seeds an
``assistant_turn(N)`` predicate assertion on the thread anchor entity.

The claim is emitted as ``"Assistant: <content>"`` exactly — Phase 3's
``window.py`` (``build_referential_window``) strips
``len("assistant") + 2 == 11`` characters off the front to reconstruct
the message content. Changing the claim format without updating the
prefix-strip is a silent window-corruption bug. Co-locate with
window.py's prefix-len comment.

Auditor-validatable confidence: same discipline as
``archive_user_turn_v1`` — ``confidence="confirmed"`` +
``derivation_type="agent_observation"`` with the turn artifact URI in
``evidence_uris``.

Optional handler_inputs:

- ``tool_calls`` — list of tool-call records (default ``[]``)
- ``finish_reason`` — provider terminate reason (default ``None``)
- ``exhausted`` — context-window exhaustion flag (default ``False``)
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..execution.resolver import NamespaceResolver, traverse_path
from .protocol import PipelineContext, StepOutput
from .registry import register_handler
from .thread_persistence import cx_async, write_turn_artifact

if TYPE_CHECKING:
    from ..schemas import StepConfig

logger = get_logger(__name__)

_LATENCY_BUDGET_MS = 50.0
_SEEDED_BY = "cortex-chat-openai"


@register_handler
class ArchiveAssistantTurnV1Handler:
    """Persist assistant turn — artifact write + confirmed cortex assertion."""

    step_type = "archive_assistant_turn_v1"

    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        start = time.monotonic()

        chat_id = context.chat_id
        if not chat_id:
            raise ValueError(
                f"Step '{step.id}': archive_assistant_turn_v1 requires context.chat_id"
            )

        resolver = NamespaceResolver(context)
        anchor_id = _resolve_required_str(resolver, step, "anchor_id")
        turn_index = _resolve_required_int(resolver, step, "turn_index")
        assistant_text = _resolve_required_str(resolver, step, "assistant_text")

        tool_calls = _resolve_optional(resolver, step, "tool_calls", default=[])
        if not isinstance(tool_calls, list):
            raise TypeError(
                f"Step '{step.id}': handler_inputs.tool_calls must resolve "
                f"to list, got {type(tool_calls).__name__}"
            )

        finish_reason = _resolve_optional(resolver, step, "finish_reason", default=None)
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise TypeError(
                f"Step '{step.id}': handler_inputs.finish_reason must resolve "
                f"to str or None, got {type(finish_reason).__name__}"
            )

        exhausted = _resolve_optional(resolver, step, "exhausted", default=False)
        if not isinstance(exhausted, bool):
            raise TypeError(
                f"Step '{step.id}': handler_inputs.exhausted must resolve to "
                f"bool, got {type(exhausted).__name__}"
            )

        artifact_uri = await write_turn_artifact(
            chat_id=chat_id,
            turn_index=turn_index,
            payload={
                "chat_id": chat_id,
                "turn_index": turn_index,
                "role": "assistant",
                "content": assistant_text,
                "tool_calls": tool_calls,
                "finish_reason": finish_reason,
                "exhausted": exhausted,
                "timestamp": time.time(),
            },
        )

        # Claim shape contract — see window.py prefix-strip comment.
        # "Assistant: " is 11 chars == len("assistant") + 2.
        claim = f"Assistant: {assistant_text}"
        assert_res = await cx_async(
            "assert",
            {
                "entity_id": anchor_id,
                "claim": claim,
                "confidence": "confirmed",
                "evidence": f"Archived assistant turn {turn_index}",
                "derivation_type": "agent_observation",
                "evidence_uris": [artifact_uri],
                "predicate_form": f"assistant_turn({turn_index})",
                "seeded_by": _SEEDED_BY,
            },
        )
        if "error" in assert_res:
            raise RuntimeError(
                f"Step '{step.id}': cortex assert failed for "
                f"assistant_turn({turn_index}) on {anchor_id}: "
                f"{assert_res.get('error')}"
            )

        assertion_id = (assert_res.get("item") or {}).get("id")
        if assertion_id is None:
            raise RuntimeError(
                f"Step '{step.id}': cortex assert returned no assertion_id "
                f"for assistant_turn({turn_index}) on {anchor_id}; "
                f"response keys={sorted(assert_res.keys())}"
            )

        duration_ms = (time.monotonic() - start) * 1000.0
        if duration_ms > _LATENCY_BUDGET_MS:
            logger.warning(
                "archive_assistant_turn_v1 latency budget blown: %.2fms "
                "(chat_id=%s, turn_index=%d)",
                duration_ms,
                chat_id,
                turn_index,
            )

        payload = {
            "artifact_uri": artifact_uri,
            "assertion_id": assertion_id,
        }
        return StepOutput(raw=json.dumps(payload), json=payload)


def _resolve_input(resolver: NamespaceResolver, step: StepConfig, name: str) -> Any:
    """Resolve a required handler_inputs binding to its final value."""
    binding = step.handler_inputs.get(name)
    if binding is None:
        raise ValueError(f"Step '{step.id}' missing handler_inputs.{name}")
    root = resolver.resolve(binding)
    return traverse_path(
        root,
        binding.field_path,
        step_name=step.id,
        field_name=name,
        binding_repr=str(binding),
        resolver=resolver,
    )


def _resolve_required_str(
    resolver: NamespaceResolver, step: StepConfig, name: str
) -> str:
    value = _resolve_input(resolver, step, name)
    if not isinstance(value, str):
        raise TypeError(
            f"Step '{step.id}': handler_inputs.{name} must resolve to str, "
            f"got {type(value).__name__}"
        )
    return value


def _resolve_required_int(
    resolver: NamespaceResolver, step: StepConfig, name: str
) -> int:
    value = _resolve_input(resolver, step, name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"Step '{step.id}': handler_inputs.{name} must resolve to int, "
            f"got {type(value).__name__}"
        )
    return value


def _resolve_optional(
    resolver: NamespaceResolver,
    step: StepConfig,
    name: str,
    *,
    default: Any,
) -> Any:
    """Resolve an optional handler_inputs binding, returning ``default``
    when no binding is provided. Resolution errors still propagate.
    """
    binding = step.handler_inputs.get(name)
    if binding is None:
        return default
    root = resolver.resolve(binding)
    return traverse_path(
        root,
        binding.field_path,
        step_name=step.id,
        field_name=name,
        binding_repr=str(binding),
        resolver=resolver,
    )
