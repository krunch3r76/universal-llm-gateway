"""Purpose-keyed CDP ``skills=`` floors (Fable leverage bind B2).

Caller ``skills=`` stays additive. Unknown / omitted purpose keeps the
judgment-only floor so existing callers do not grow an arch pair.
"""

from __future__ import annotations

from claude_bundles.chat_model_match import normalize_picker_request

CDP_PURPOSE_PROFILES: dict[str, tuple[str, ...]] = {
    "ask": (
        "architecture-invariants",
        "ulg-architecture",
        "ulg-for-llms",
        "reasoning-posture",
        "hypothesize-simulate",
    ),
    "review": (
        "ulg-for-llms",
        "reasoning-posture",
        "consult-posture",
        "hypothesize-simulate",
    ),
    "produce": ("ulg-for-llms", "reasoning-posture"),
    "mission": (
        "cdp-operator-proxy",
        "ulg-for-llms",
        "reasoning-posture",
        "hypothesize-simulate",
    ),
    "operator-proxy": (
        "cdp-operator-proxy",
        "ulg-for-llms",
        "reasoning-posture",
        "hypothesize-simulate",
    ),
}

_DEFAULT_FLOOR: tuple[str, ...] = ("ulg-for-llms", "reasoning-posture")


def infer_cdp_purpose(purpose: str | None, model: str | None) -> str:
    """Explicit purpose wins; omitted + Sonnet → produce; else ask."""
    raw = (purpose or "").strip()
    if raw:
        return raw
    picker = normalize_picker_request(model or "").lower()
    if picker.startswith("sonnet"):
        return "produce"
    return "ask"


def profile_slugs_for_purpose(purpose: str | None) -> tuple[str, ...]:
    """Return the staging floor for a purpose tag.

    ``None`` / blank / unknown → judgment skill only (pre-B2 behavior).
    """
    key = (purpose or "").strip().lower()
    if not key:
        return _DEFAULT_FLOOR
    return CDP_PURPOSE_PROFILES.get(key, _DEFAULT_FLOOR)
