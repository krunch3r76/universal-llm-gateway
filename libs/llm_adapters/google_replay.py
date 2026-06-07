"""Lossless Gemini conversation replay helpers.

Preserves model-turn parts (including ``thoughtSignature``, ``functionCall``
ids, and unknown auxiliary keys) when appending tool rounds. Normalizes
empty text parts that Gemini rejects on replay.
"""

from __future__ import annotations

import json
import os
from typing import Any

from universal_logging import DEBUG, get_logger

logger = get_logger(__name__)

_TRACE_ENV = "GEMINI_TOOL_LOOP_TRACE"


def normalize_gemini_parts(parts: list[Any]) -> list[dict[str, Any]]:
    """Drop empty text-only parts; copy all other parts verbatim."""
    normalized: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if set(part.keys()) == {"text"} and not str(part.get("text", "")):
            continue
        if "text" in part and not part.get("text") and not part.get("thought"):
            if not any(
                k in part
                for k in ("functionCall", "functionResponse", "thoughtSignature")
            ):
                continue
        normalized.append(dict(part))
    return normalized


def replay_model_turn_content(raw_response: dict[str, Any]) -> dict[str, Any] | None:
    """Return the model turn from a generateContent response for history replay."""
    candidates = raw_response.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        return None
    content = candidate.get("content")
    if not isinstance(content, dict):
        return None
    parts = content.get("parts")
    if not isinstance(parts, list):
        return dict(content)
    role = content.get("role") or "model"
    return {"role": role, "parts": normalize_gemini_parts(parts)}


def log_tool_round_trace(
    *,
    phase: str,
    contents: list[Any],
    tool_results: list[dict[str, Any]] | None = None,
) -> None:
    """Emit redacted debug trace when ``GEMINI_TOOL_LOOP_TRACE=1``."""
    if os.environ.get(_TRACE_ENV) != "1":
        return
    redacted_contents = _redact_contents(contents)
    payload: dict[str, Any] = {"phase": phase, "contents": redacted_contents}
    if tool_results is not None:
        payload["tool_results"] = [
            {
                "id": tr.get("id"),
                "name": tr.get("name"),
                "content_len": len(str(tr.get("content", ""))),
            }
            for tr in tool_results
        ]
    logger.log(DEBUG, "gemini_tool_loop_trace %s", json.dumps(payload, default=str))


def _redact_contents(contents: list[Any]) -> list[Any]:
    redacted: list[Any] = []
    for turn in contents:
        if not isinstance(turn, dict):
            redacted.append(turn)
            continue
        parts = turn.get("parts")
        if not isinstance(parts, list):
            redacted.append({"role": turn.get("role"), "parts": "[non-list]"})
            continue
        summary_parts: list[Any] = []
        for part in parts:
            if not isinstance(part, dict):
                summary_parts.append(part)
                continue
            if "text" in part:
                text = str(part.get("text", ""))
                summary_parts.append(
                    {
                        **{k: v for k, v in part.items() if k != "text"},
                        "text": f"[len={len(text)}]",
                    }
                )
            elif "functionResponse" in part:
                fr = part.get("functionResponse") or {}
                summary_parts.append(
                    {
                        "functionResponse": {
                            "name": fr.get("name"),
                            "id": fr.get("id"),
                            "response_keys": sorted((fr.get("response") or {}).keys()),
                        }
                    }
                )
            else:
                summary_parts.append(
                    {k: v for k, v in part.items() if k != "thoughtSignature"}
                )
        redacted.append({"role": turn.get("role"), "parts": summary_parts})
    return redacted
