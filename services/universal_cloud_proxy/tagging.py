"""Capability tagging and quality-tier classification for model selection.

Tags are derived from model ID patterns + modality. The mapping is
intentionally conservative — only tag what the ID unambiguously signals.
Shared by both browser (OpenRouter) and local (Stargate) catalog caches.

Multimodal tags (vision, audio, video) indicate additional capabilities, not
reduced text quality. They are included in selection results by default and
only excluded when the caller explicitly passes them in exclude_tags.

Quality tiers (0-3) rank models by cost/quality ratio:
  0 = free cloud junk, 1 = low, 2 = mid, 3 = high (sweet spot $5-30/M).
  Ultra-premium (>=$30/M) is capped to tier 2 — legacy/retiring models
  with diminishing returns over tier 3.
"""

from __future__ import annotations

import re

_FAST_CLASS_PATTERN = re.compile(r"\b(?:flash|lite|mini|fast)\b", re.IGNORECASE)

_ID_TAG_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"code[xr]?|coder", re.IGNORECASE), "code"),
    (_FAST_CLASS_PATTERN, "fast"),
    (
        re.compile(r"thinking|:thinking|deepseek-r1|^openai/o[134]", re.IGNORECASE),
        "reasoning",
    ),
    (re.compile(r"-pro(?:$|-|\b)", re.IGNORECASE), "pro"),
    (re.compile(r"chat\b", re.IGNORECASE), "chat"),
    # -mcp suffix: requires agentic tool-call execution loop; not for chat UIs.
    (re.compile(r"-mcp$", re.IGNORECASE), "agentic"),
]

# ── Tier thresholds (completion cost per million tokens) ─────────────
_TIER_LOW_CEIL = 0.5
_TIER_MID_CEIL = 5.0
_TIER_HIGH_CEIL = 30.0  # >= this → ultra-premium, capped to tier 2

# Name-based tier boosts: model ID patterns → minimum tier 3
_TIER_BOOST_RULES: list[re.Pattern[str]] = [
    re.compile(r"sonnet", re.IGNORECASE),
    re.compile(r"gpt-4o(?!-mini)", re.IGNORECASE),
]

# Name-based tier caps: model ID patterns → maximum tier 2
_TIER_CAP_RULES: list[re.Pattern[str]] = [
    _FAST_CLASS_PATTERN,
    re.compile(r"haiku", re.IGNORECASE),
]


def derive_tags(model_id: str, modality: str = "") -> list[str]:
    """Derive capability tags from model ID and modality."""
    tags: list[str] = []
    for pattern, tag in _ID_TAG_RULES:
        if pattern.search(model_id):
            tags.append(tag)
    if "image" in modality:
        tags.append("vision")
    if "audio" in modality:
        tags.append("audio")
    if "video" in modality:
        tags.append("video")
    if not tags:
        tags.append("general")
    return sorted(set(tags))


def derive_tier(
    model_id: str,
    completion_cost: float,
    source: str,
) -> int:
    """Classify model into quality tier 0-3.

    Primary signal: completion cost thresholds.
    Overrides: name heuristics (boost/cap), source (local = min tier 2),
    ultra-premium ceiling (>=$30/M capped to tier 2).
    """
    if source == "local":
        return 2

    # Cost-based classification
    if completion_cost <= 0:
        tier = 0
    elif completion_cost < _TIER_LOW_CEIL:
        tier = 1
    elif completion_cost < _TIER_MID_CEIL:
        tier = 2
    elif completion_cost < _TIER_HIGH_CEIL:
        tier = 3
    else:
        tier = 2  # ultra-premium: capped — diminishing returns

    # Name-based caps (applied before boosts to prevent boost overriding cap)
    if any(p.search(model_id) for p in _TIER_CAP_RULES):
        tier = min(tier, 2)
        return tier

    # Name-based boosts (only within cost ceiling)
    if completion_cost < _TIER_HIGH_CEIL and any(
        p.search(model_id) for p in _TIER_BOOST_RULES
    ):
        tier = max(tier, 3)

    return tier
