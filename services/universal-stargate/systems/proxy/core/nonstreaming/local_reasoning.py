"""Map OpenRouter-style reasoning knobs onto local chat-template thinking.

Pipelines toggle thinking via profiles (`chat_template_kwargs.enable_thinking`).
One-shot / ralph editor seats should reuse the same OpenRouter body shape:

    {"reasoning": {"effort": "none"}}

or top-level ``reasoning_effort: "none"`` — without requiring ``?profile=``.
"""

from __future__ import annotations

from typing import Any

_REASONING_OFF = frozenset({"none", "minimal"})


def is_local_model_id(model_id: str) -> bool:
    """Local seats have bare IDs; cloud/OpenRouter use ``provider/model``."""
    return "/" not in (model_id or "").strip()


def extract_reasoning_effort(request_data: dict[str, Any]) -> str | None:
    """Return normalized effort string from body, or None if absent."""
    reasoning = request_data.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort") is not None:
        return str(reasoning["effort"]).strip().lower()
    top = request_data.get("reasoning_effort")
    if top is not None and str(top).strip():
        return str(top).strip().lower()
    return None


def apply_local_reasoning_off(request_data: dict[str, Any], *, model_id: str) -> bool:
    """For local models, map reasoning-off → ``enable_thinking: false``.

    Returns True when a mutation was applied. Does not strip ``reasoning``
    (harmless on llama.cpp; required passthrough on cloud paths that skip this).
    Explicit caller ``chat_template_kwargs.enable_thinking`` is overwritten when
    effort is off — ``reasoning.effort=none`` is the authoritative disable signal.
    """
    if not is_local_model_id(model_id):
        return False
    effort = extract_reasoning_effort(request_data)
    if effort not in _REASONING_OFF:
        return False
    ctk = request_data.get("chat_template_kwargs")
    if not isinstance(ctk, dict):
        ctk = {}
        request_data["chat_template_kwargs"] = ctk
    if ctk.get("enable_thinking") is False:
        return False
    ctk["enable_thinking"] = False
    return True
