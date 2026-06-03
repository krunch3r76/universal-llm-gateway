"""Google Gemini ``streamGenerateContent`` SSE reducer.

Wire protocol (from ``?alt=sse`` endpoint):

    data: <complete GenerateContentResponse>\\n\\n
    data: <complete GenerateContentResponse>\\n\\n
    ...
    data: <complete GenerateContentResponse with finishReason>\\n\\n

Every ``data:`` frame is a *complete* JSON ``GenerateContentResponse`` — not a
delta envelope. The individual text parts *inside* those responses, however,
ARE deltas that must be concatenated across frames to reconstruct the full
answer; ``functionCall`` parts are atomic (one part, one call, one frame).

Cross-chunk JSON assembly is therefore handled by the SSE framing layer
(``libs/sse/framing.iter_sse_events``) via the W3C event-boundary protocol.
This reducer operates on already-parsed SSE events and accumulates the
content-part stream from ``candidates[0].content.parts`` across frames.

Stream terminus:

    Gemini's SSE stream does not emit an explicit terminal event (no
    equivalent to Anthropic's ``message_stop`` or OpenAI's
    ``response.completed``). The server signals completion by the final
    frame carrying ``candidates[0].finishReason`` and then closing the TCP
    stream (``iter_sse_events`` exhausted). ``reduce`` therefore always
    returns False; ``accumulate_sse_stream`` returns the terminal state when
    the byte iterator is exhausted.

Output shape:

    ``to_terminal_dict`` returns a single synthesized ``GenerateContentResponse``
    shape matching what ``libs/llm_adapters/google.py:GoogleAdapter.parse_frontier_response``
    expects:

        {
            "candidates": [{
                "content": {"parts": [...], "role": "model"},
                "finishReason": "STOP" | "MAX_TOKENS" | ...,
                "groundingMetadata": {...} | absent,
            }],
            "usageMetadata": {...},
            "modelVersion": "...",
            "responseId": "...",
            "promptFeedback": {...} | absent,
        }

Text-delta merge rule:

    Gemini's text-part deltas arrive as separate ``{"text": "..."}`` entries in
    consecutive frames. Adjacent text parts with the same ``thought`` flag are
    merged into a single accumulated part; a change in ``thought`` flag — or
    the arrival of a non-text part (``functionCall``, ``executableCode``, etc.)
    — closes the current run. This matches how downstream parsers (and the
    non-streaming ``generateContent`` response) present the content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sse.protocols import SSEMessage, SSEProviderError
from universal_logging import WARNING, get_logger

from llm_adapters.google_replay import normalize_gemini_parts

logger = get_logger(__name__)


@dataclass
class _GoogleState:
    """Mutable reducer state for a single Gemini streamGenerateContent response."""

    # Accumulated parts of candidates[0].content — text deltas merged, other
    # parts appended as-is.
    parts: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    finish_message: str | None = None
    grounding_metadata: dict[str, Any] | None = None

    # Response-level metadata — last frame wins (the final frame carries
    # authoritative totals for usageMetadata).
    usage_metadata: dict[str, Any] = field(default_factory=dict)
    model_version: str = ""
    response_id: str = ""
    prompt_feedback: dict[str, Any] = field(default_factory=dict)
    role: str = "model"


class GoogleStreamReducer:
    """Reduce Gemini streamGenerateContent SSE events to the native response shape.

    Each SSE event's ``data`` is a complete ``GenerateContentResponse`` JSON
    object. The reducer merges per-frame ``candidates[0].content.parts`` into
    a single accumulated part list and tracks last-value response metadata.
    """

    DEFAULT_STALL_TIMEOUT: float = 60.0

    def initial_state(self) -> _GoogleState:
        return _GoogleState()

    def reduce(self, state: _GoogleState, event: SSEMessage) -> bool:
        """Mutate state with one GenerateContentResponse frame.

        Always returns False — Gemini has no explicit terminal event; the
        stream ends when the byte iterator is exhausted by the server closing
        the connection after the frame carrying ``finishReason``.
        """
        payload = _parse_payload(event)
        if not payload:
            if event.data:
                logger.log(
                    WARNING,
                    "google stream reducer: dropped unparseable SSE frame (%d bytes)",
                    len(str(event.data)),
                )
            return False

        candidates = payload.get("candidates")
        if isinstance(candidates, list) and candidates:
            candidate = candidates[0]
            if isinstance(candidate, dict):
                self._absorb_candidate(state, candidate)

        usage = payload.get("usageMetadata")
        if isinstance(usage, dict):
            # Final frame carries the authoritative totals; last-value-wins.
            state.usage_metadata = dict(usage)

        model_version = payload.get("modelVersion")
        if isinstance(model_version, str) and model_version:
            state.model_version = model_version

        response_id = payload.get("responseId")
        if isinstance(response_id, str) and response_id:
            state.response_id = response_id

        prompt_feedback = payload.get("promptFeedback")
        if isinstance(prompt_feedback, dict):
            state.prompt_feedback = dict(prompt_feedback)

        return False

    def terminal_error(self, state: _GoogleState, event: SSEMessage) -> None:
        _ = state
        try:
            payload = _parse_payload(event)
        except (json.JSONDecodeError, TypeError):
            payload = {"raw": event.data}
        raise SSEProviderError(f"Google stream error: {payload}")

    @staticmethod
    def to_terminal_dict(state: _GoogleState) -> dict[str, Any]:
        """Build the synthesized GenerateContentResponse for parse_frontier_response."""
        if not state.parts and state.finish_reason is None:
            # No candidate data arrived — return an empty-but-well-formed
            # response so downstream parsing does not crash on missing keys.
            return {
                "candidates": [],
                "usageMetadata": state.usage_metadata,
                "modelVersion": state.model_version,
                "responseId": state.response_id,
                "promptFeedback": state.prompt_feedback,
            }

        candidate: dict[str, Any] = {
            "content": {
                "parts": normalize_gemini_parts(state.parts),
                "role": state.role,
            },
            "index": 0,
        }
        if state.finish_reason is not None:
            candidate["finishReason"] = state.finish_reason
        if state.finish_message is not None:
            candidate["finishMessage"] = state.finish_message
        if state.grounding_metadata is not None:
            candidate["groundingMetadata"] = state.grounding_metadata

        result: dict[str, Any] = {"candidates": [candidate]}
        if state.usage_metadata:
            result["usageMetadata"] = state.usage_metadata
        if state.model_version:
            result["modelVersion"] = state.model_version
        if state.response_id:
            result["responseId"] = state.response_id
        if state.prompt_feedback:
            result["promptFeedback"] = state.prompt_feedback
        return result

    @staticmethod
    def _absorb_candidate(state: _GoogleState, candidate: dict[str, Any]) -> None:
        """Merge candidate's content.parts and terminal metadata into state."""
        content = candidate.get("content")
        if isinstance(content, dict):
            role = content.get("role")
            if isinstance(role, str) and role:
                state.role = role
            parts = content.get("parts")
            if isinstance(parts, list):
                GoogleStreamReducer._merge_parts(state, parts)

        finish_reason = candidate.get("finishReason")
        if isinstance(finish_reason, str) and finish_reason:
            state.finish_reason = finish_reason

        finish_message = candidate.get("finishMessage")
        if isinstance(finish_message, str) and finish_message:
            state.finish_message = finish_message

        grounding = candidate.get("groundingMetadata")
        if isinstance(grounding, dict):
            state.grounding_metadata = grounding

    @staticmethod
    def _merge_parts(state: _GoogleState, parts: list[Any]) -> None:
        """Merge a frame's content.parts into state.parts.

        Text parts are deltas and concatenate with the current open text run
        when the ``thought`` flag matches; any other part (``functionCall``,
        ``executableCode``, ``codeExecutionResult``, ``inlineData``, ...) is
        appended unchanged and closes the current text run.
        """
        for part in parts:
            if not isinstance(part, dict):
                continue

            if "text" in part:
                is_thought = bool(part.get("thought"))
                extra_keys = set(part.keys()) - {"text", "thought"}
                merged = False
                if not extra_keys and state.parts:
                    last = state.parts[-1]
                    # Merge only if the last part is a plain text delta run
                    # with matching thought flag; auxiliary keys (e.g.
                    # ``thoughtSignature``) force a new part.
                    if (
                        isinstance(last, dict)
                        and set(last.keys()).issubset({"text", "thought"})
                        and "text" in last
                        and bool(last.get("thought")) == is_thought
                    ):
                        last["text"] = str(last.get("text", "")) + str(
                            part.get("text", "")
                        )
                        merged = True
                if not merged:
                    state.parts.append(dict(part))
            else:
                state.parts.append(dict(part))


def _parse_payload(event: SSEMessage) -> dict[str, Any]:
    if isinstance(event.data, dict):
        return event.data
    try:
        loaded = json.loads(event.data)
    except (json.JSONDecodeError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}
