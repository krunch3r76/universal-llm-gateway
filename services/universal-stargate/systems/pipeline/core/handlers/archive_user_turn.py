"""``archive_user_turn_v1`` — persist the user turn (artifact + assertion).

Always-on archive step of the cortex-chat-openai compactor. Writes the
user turn's JSON artifact to ``workspaces://.runtime/thread-artifacts/``
and seeds a ``user_turn(N)`` predicate assertion on the thread anchor
entity so the next request's ``assemble_thread_v1`` step can read it
back as part of the referential window.

The claim is emitted as ``"User: <content>"`` exactly — Phase 3's
``window.py`` (``build_referential_window``) strips ``len("user") + 2 == 6``
characters off the front to reconstruct the message content. Changing
the claim format without updating the prefix-strip is a silent
window-corruption bug. Co-locate with window.py's prefix-len comment.

Auditor-validatable confidence: each archive assertion is written
``confidence="confirmed"`` + ``derivation_type="agent_observation"``
with the turn artifact URI in ``evidence_uris``. This is a runtime
observation (a turn happened), not an inference, and the artifact URI
is the file-system witness. Closes the
``confirmed_entity_no_assertions`` gap that Phase 3's anchor creation
leaves open (the first archive on a fresh anchor backs the
``status="confirmed"`` entity with a confirmed assertion in the same
request).
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
class ArchiveUserTurnV1Handler:
    """Persist user turn — artifact write + confirmed cortex assertion."""

    step_type = "archive_user_turn_v1"

    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        start = time.monotonic()

        chat_id = context.chat_id
        if not chat_id:
            raise ValueError(
                f"Step '{step.id}': archive_user_turn_v1 requires context.chat_id"
            )

        resolver = NamespaceResolver(context)
        anchor_id = _resolve_required_str(resolver, step, "anchor_id")
        turn_index = _resolve_required_int(resolver, step, "turn_index")
        user_text = _resolve_required_str(resolver, step, "user_text")

        artifact_uri = await write_turn_artifact(
            chat_id=chat_id,
            turn_index=turn_index,
            payload={
                "chat_id": chat_id,
                "turn_index": turn_index,
                "role": "user",
                "content": user_text,
                "timestamp": time.time(),
            },
        )

        # Claim shape contract — see window.py prefix-strip comment.
        # "User: " is 6 chars == len("user") + 2.
        claim = f"User: {user_text}"
        assert_res = await cx_async(
            "assert",
            {
                "entity_id": anchor_id,
                "claim": claim,
                "confidence": "confirmed",
                "evidence": f"Archived user turn {turn_index}",
                "derivation_type": "agent_observation",
                "evidence_uris": [artifact_uri],
                "predicate_form": f"user_turn({turn_index})",
                "seeded_by": _SEEDED_BY,
            },
        )
        if "error" in assert_res:
            raise RuntimeError(
                f"Step '{step.id}': cortex assert failed for "
                f"user_turn({turn_index}) on {anchor_id}: "
                f"{assert_res.get('error')}"
            )

        assertion_id = (assert_res.get("item") or {}).get("id")
        if assertion_id is None:
            raise RuntimeError(
                f"Step '{step.id}': cortex assert returned no assertion_id "
                f"for user_turn({turn_index}) on {anchor_id}; response keys="
                f"{sorted(assert_res.keys())}"
            )

        duration_ms = (time.monotonic() - start) * 1000.0
        if duration_ms > _LATENCY_BUDGET_MS:
            logger.warning(
                "archive_user_turn_v1 latency budget blown: %.2fms "
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
