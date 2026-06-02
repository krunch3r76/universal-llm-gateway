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
from typing import TYPE_CHECKING

from universal_logging import get_logger

from ..events.compaction import PipelineCompactionArchived
from ..execution.resolver import NamespaceResolver
from ._handler_input_resolve import _resolve_required_int, _resolve_required_str
from .protocol import PipelineContext, StepOutput
from .registry import register_handler
from .thread_persistence import (
    cx_async,
    publish_compaction_event,
    require_thread_binding,
    write_turn_artifact,
)

if TYPE_CHECKING:
    from ..schemas import StepConfig

logger = get_logger(__name__)

_LATENCY_BUDGET_MS = 50.0


@register_handler
class ArchiveUserTurnV1Handler:
    """Persist user turn — artifact write + confirmed cortex assertion."""

    step_type = "archive_user_turn_v1"

    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        start = time.monotonic()

        binding = require_thread_binding(context)
        storage_key = binding.storage_key
        seeded_by = context.pipeline.id

        resolver = NamespaceResolver(context)
        anchor_id = _resolve_required_str(resolver, step, "anchor_id")
        turn_index = _resolve_required_int(resolver, step, "turn_index")
        user_text = _resolve_required_str(resolver, step, "user_text")

        artifact_uri = await write_turn_artifact(
            chat_id=storage_key,
            turn_index=turn_index,
            payload={
                "thread_key": storage_key,
                "thread_kind": binding.kind,
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
                "seeded_by": seeded_by,
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

        publish_compaction_event(
            context,
            PipelineCompactionArchived,
            execution_id=context.execution_id,
            chat_id=storage_key,
            anchor_id=anchor_id,
            turn_index=turn_index,
            role="user",
            artifact_uri=artifact_uri,
            assertion_id=assertion_id,
        )

        duration_ms = (time.monotonic() - start) * 1000.0
        if duration_ms > _LATENCY_BUDGET_MS:
            logger.warning(
                "archive_user_turn_v1 latency budget blown: %.2fms "
                "(chat_id=%s, turn_index=%d)",
                duration_ms,
                storage_key,
                turn_index,
            )

        payload = {
            "artifact_uri": artifact_uri,
            "assertion_id": assertion_id,
        }
        return StepOutput(raw=json.dumps(payload), json=payload)
