"""Opus High picker retry then Fable fallback (a:31534).

After-ship ``cdp/opus-5`` overlays were dying on the first
``model select failed`` and leaving ``observer_unverified`` / unread harvest.
Retry the same Opus High request once; if the picker still misses, one
``fable-5`` attempt marked ``family_substituted``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")

FABLE_FALLBACK_MODEL = "fable-5"


def is_model_select_error(error: str | None) -> bool:
    """True when the harness failed in the model picker."""
    return "model select failed" in (error or "").lower()


def is_opus_high_request(model: str) -> bool:
    """True when the request is Opus family at sealed/explicit High."""
    from claude_bundles.chat_model_match import (
        normalize_picker_request,
        parse_model_request,
        sealed_ask_default_effort,
    )

    raw = (model or "").strip()
    if not raw:
        return False
    wire = normalize_picker_request(raw)
    family, effort = parse_model_request(wire)
    if effort is None:
        effort = sealed_ask_default_effort(family)
    return family.startswith("opus") and effort == "high"


async def run_with_overlay_retry(
    *,
    requested_model: str,
    run_once: Callable[[str], Awaitable[T]],
    error_of: Callable[[T], str | None],
    ok_of: Callable[[T], bool],
) -> tuple[T, dict[str, Any]]:
    """Run ``run_once``; on Opus High picker miss, retry once then Fable.

    The first failure is never the terminal result while a retry remains.
    """
    result = await run_once(requested_model)
    extras: dict[str, Any] = {
        "overlay_select_attempts": [requested_model],
        "family_substituted": False,
    }
    if ok_of(result) or not is_model_select_error(error_of(result)):
        return result, extras
    if not is_opus_high_request(requested_model):
        return result, extras

    retry = await run_once(requested_model)
    extras["overlay_select_attempts"].append(requested_model)
    if ok_of(retry) or not is_model_select_error(error_of(retry)):
        extras["overlay_retry"] = "opus_high"
        return retry, extras

    fallback = await run_once(FABLE_FALLBACK_MODEL)
    extras["overlay_select_attempts"].append(FABLE_FALLBACK_MODEL)
    extras["family_substituted"] = True
    extras["overlay_retry"] = "fable"
    extras["requested_model"] = requested_model
    extras["resolved_model"] = FABLE_FALLBACK_MODEL
    return fallback, extras
