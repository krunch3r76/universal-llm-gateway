"""``summarize_thread_v1`` — collapse older turns into a cortex consolidation summary.

Runs after ``archive_assistant_turn_v1`` in the cortex-chat-openai pipeline.
Selects non-superseded user/assistant turn assertions older than the hot window,
loads per-turn artifact JSON to enrich the summary input with tool outcomes,
calls a cheap summarizer model, writes a ``archive summary: …`` claim (§6.10
consolidation prefix), and emits ``pipeline.compaction.summarized``.

Stage A (default): summary written; collapsed turns NOT superseded
(``supersede_collapsed=false``). The summary is injected into the assembled
prefix by ``build_referential_window`` so the model has compressed prior context.

Stage C (future opt-in): set ``supersede_collapsed=true`` to supersede collapsed
turns after the summary is written (full compaction). Partial supersede is a hard
error — the handler raises immediately if any supersede call fails.

No-ops when:
- ``turn_index <= window_size + summarize_margin`` (thread too short)
- A ``thread_summary(N)`` predicate already covers the collapse boundary
  (idempotency guard — safe to retry)

Domain fields (YAML step-level):
- ``window_size`` (int, default 8) — hot-tail size; turns kept as raw assertions
- ``summarize_margin`` (int, default 4) — extra buffer before triggering
- ``summary_model`` (str, default ``"openai/gpt-5.5-mini"``) — summarizer model
- ``supersede_collapsed`` (bool, default False) — Stage A=false; Stage C=true

Handler inputs (bound via ``handler_inputs:`` in YAML):
- ``anchor_id`` (str) — thread anchor entity ID (from ``assemble.json.anchor_id``)
- ``turn_index`` (int) — next free slot on the anchor
  (from ``assemble.json.turn_index``)

Handler outputs (JSON namespace):
- ``summary_assertion_id`` (int | str | None) — cortex assertion ID, None on no-op
- ``turns_summarized`` (int) — superseded turn count; 0 when supersede_collapsed=false
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from universal_logging import get_logger

from ..events.compaction import (
    PipelineCompactionArtifactLoadSkipped,
    PipelineCompactionSummarized,
    PipelineCompactionSupersedeFailed,
)
from ..execution.resolver import NamespaceResolver
from ._handler_input_resolve import _resolve_required_int, _resolve_required_str
from .builtin.call_model import call_model
from .protocol import PipelineContext, StepOutput
from .registry import register_handler
from .thread_persistence import (
    cx_async,
    publish_compaction_event,
    require_thread_binding,
)
from .thread_persistence.compaction_summarize import (
    build_summary_input,
    is_already_summarized,
    load_collapse_set_artifacts,
    select_collapse_set,
    supersede_collapsed_turns,
    write_summary_assertion,
)

if TYPE_CHECKING:
    from ..schemas import StepConfig

logger = get_logger(__name__)

_DEFAULT_WINDOW_SIZE = 8
_DEFAULT_SUMMARIZE_MARGIN = 4
_DEFAULT_SUMMARY_MODEL = "openai/gpt-5.5-mini"

_SYSTEM_PROMPT = (
    "You are a conversation summarizer. "
    "Produce a faithful, compressed narrative of the conversation turns provided. "
    "Preserve all decisions, names, tool outcomes, and key facts. "
    "Do not add new facts or inferences. "
    "Be concise."
)


@register_handler
class SummarizeThreadV1Handler:
    """Collapse old turn assertions into a §6.10 consolidation summary + supersede."""

    step_type = "summarize_thread_v1"

    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        start = time.monotonic()

        binding = require_thread_binding(context)
        storage_key = binding.storage_key
        seeded_by = context.pipeline.id

        resolver = NamespaceResolver(context)
        anchor_id = _resolve_required_str(resolver, step, "anchor_id")
        turn_index = _resolve_required_int(resolver, step, "turn_index")

        window_size = _resolve_window_size(step)
        summarize_margin = _resolve_summarize_margin(step)
        summary_model = str(
            step.get_domain_field("summary_model", _DEFAULT_SUMMARY_MODEL)
            or _DEFAULT_SUMMARY_MODEL
        )
        supersede_collapsed = bool(step.get_domain_field("supersede_collapsed", False))

        threshold = window_size + summarize_margin
        if turn_index <= threshold:
            logger.debug(
                "summarize_thread_v1 no-op: turn_index=%d <= threshold=%d "
                "(window=%d, margin=%d, anchor=%s)",
                turn_index,
                threshold,
                window_size,
                summarize_margin,
                anchor_id,
            )
            return _noop_output()

        collapse_up_to = turn_index - window_size

        all_assertions = await _load_all_assertions(step, anchor_id)

        if is_already_summarized(all_assertions, collapse_up_to):
            logger.debug(
                "summarize_thread_v1 idempotent skip: collapse_up_to=%d "
                "already covered (anchor=%s)",
                collapse_up_to,
                anchor_id,
            )
            return _noop_output()

        collapse_set = select_collapse_set(all_assertions, collapse_up_to)
        if not collapse_set:
            logger.debug(
                "summarize_thread_v1 no-op: empty collapse set at boundary=%d "
                "(anchor=%s)",
                collapse_up_to,
                anchor_id,
            )
            return _noop_output()

        artifacts, artifact_stats = await load_collapse_set_artifacts(collapse_set)
        if artifact_stats.skipped > 0:
            sample_uri: str | None = None
            for ass in collapse_set:
                uris = ass.get("evidence_uris") or []
                if uris:
                    sample_uri = str(uris[0])
                    break
            publish_compaction_event(
                context,
                PipelineCompactionArtifactLoadSkipped,
                execution_id=context.execution_id,
                chat_id=storage_key,
                anchor_id=anchor_id,
                attempted=artifact_stats.attempted,
                loaded=artifact_stats.loaded,
                skipped=artifact_stats.skipped,
                skip_reasons=artifact_stats.skip_reasons,
                sample_uri=sample_uri,
            )
        summary_input = build_summary_input(collapse_set, artifacts=artifacts)
        model_result = await call_model(
            model_id=summary_model,
            prompt=summary_input,
            step=step,
            context=context,
            system_prompt=_SYSTEM_PROMPT,
            call_label="thread_summarize",
        )
        summary_text = model_result.content.strip()

        assert_res = await write_summary_assertion(
            anchor_id=anchor_id,
            summary_text=summary_text,
            batch_turn_index=collapse_up_to,
            collapse_set=collapse_set,
            seeded_by=seeded_by,
        )
        if "error" in assert_res:
            raise RuntimeError(
                f"Step '{step.id}': cortex assert failed for "
                f"thread_summary({collapse_up_to}) on {anchor_id}: "
                f"{assert_res.get('error')}"
            )

        summary_assertion_id = (assert_res.get("item") or {}).get("id")
        if summary_assertion_id is None:
            raise RuntimeError(
                f"Step '{step.id}': cortex assert returned no assertion_id "
                f"for thread_summary({collapse_up_to}) on {anchor_id}; "
                f"response keys={sorted(assert_res.keys())}"
            )

        if supersede_collapsed:
            # Stage C: fail loud on partial supersede.
            try:
                turns_superseded = await supersede_collapsed_turns(
                    collapse_set=collapse_set,
                    summary_assertion_id=summary_assertion_id,
                    seeded_by=seeded_by,
                )
            except RuntimeError as exc:
                logger.error(
                    "summarize_thread_v1: partial supersede — summary=%s written "
                    "but turns not fully superseded (anchor=%s, collapse_up_to=%d)",
                    summary_assertion_id,
                    anchor_id,
                    collapse_up_to,
                )
                publish_compaction_event(
                    context,
                    PipelineCompactionSupersedeFailed,
                    execution_id=context.execution_id,
                    chat_id=storage_key,
                    anchor_id=anchor_id,
                    summary_assertion_id=summary_assertion_id,
                    collapse_up_to=collapse_up_to,
                    superseded_count=0,
                    collapse_set_size=len(collapse_set),
                    error=str(exc),
                )
                raise
        else:
            # Stage A: summary written, turns retained (no supersede).
            turns_superseded = 0
            logger.debug(
                "summarize_thread_v1: Stage A (supersede_collapsed=false) — "
                "summary=%s written; %d turns retained (anchor=%s, boundary=%d)",
                summary_assertion_id,
                len(collapse_set),
                anchor_id,
                collapse_up_to,
            )

        publish_compaction_event(
            context,
            PipelineCompactionSummarized,
            execution_id=context.execution_id,
            chat_id=storage_key,
            anchor_id=anchor_id,
            turns_summarized=turns_superseded,
            summary_assertion_id=summary_assertion_id,
        )

        duration_ms = (time.monotonic() - start) * 1000.0
        logger.info(
            "summarize_thread_v1: collapsed %d turns → summary=%s "
            "(anchor=%s, boundary=%d, %.2fms)",
            turns_superseded,
            summary_assertion_id,
            anchor_id,
            collapse_up_to,
            duration_ms,
        )

        payload = {
            "summary_assertion_id": summary_assertion_id,
            "turns_summarized": turns_superseded,
        }
        return StepOutput(raw=json.dumps(payload), json=payload)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _noop_output() -> StepOutput:
    payload = {"summary_assertion_id": None, "turns_summarized": 0}
    return StepOutput(raw=json.dumps(payload), json=payload)


def _resolve_window_size(step: StepConfig) -> int:
    raw = step.get_domain_field("window_size", _DEFAULT_WINDOW_SIZE)
    try:
        v = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Step '{step.id}': window_size must be an integer, got {raw!r}"  # type: ignore[attr-defined]
        ) from exc
    if v < 1:
        raise ValueError(f"Step '{step.id}': window_size must be >= 1, got {v}")  # type: ignore[attr-defined]
    return v


def _resolve_summarize_margin(step: StepConfig) -> int:
    raw = step.get_domain_field("summarize_margin", _DEFAULT_SUMMARIZE_MARGIN)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Step '{step.id}': summarize_margin must be an integer, got {raw!r}"  # type: ignore[attr-defined]
        ) from exc


async def _load_all_assertions(step: StepConfig, anchor_id: str) -> list[dict]:
    """Load all assertions on the anchor for idempotency + collapse set selection."""
    res = await cx_async("entity_get", {"entity_id": anchor_id})
    if "error" in res:
        raise RuntimeError(
            f"Step '{step.id}': failed to load anchor {anchor_id}: {res['error']}"  # type: ignore[attr-defined]
        )
    return res.get("assertions") or []
