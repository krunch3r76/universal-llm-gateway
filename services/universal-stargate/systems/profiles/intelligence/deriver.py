"""Derive intelligence profiles from enriched cloud proxy metadata.

Maps the enriched fields from the browser catalog (description, architecture,
supported_parameters, top_provider, pricing, tags, tier) into structured
IntelligenceProfile instances for each cloud model.

Designed to run when the cloud proxy catalog is refreshed. Each derived
profile is keyed by the cloud model ID (e.g. 'qwen/qwen3-32b').
"""

from __future__ import annotations

import re
from typing import Any

from intelligence_profiles import DomainScore, Evidence, IntelligenceProfile

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "code": ["code", "programming", "developer", "software", "coding", "coder"],
    "math": ["math", "mathematical", "arithmetic", "calculation"],
    "reasoning": ["reason", "logic", "analytical", "thinking", "chain-of-thought"],
    "creative_writing": ["creative", "writing", "story", "fiction", "poetry"],
    "summarization": ["summar", "condensing", "tldr"],
    "translation": ["translat", "multilingual", "polyglot"],
    "science": ["science", "scientific", "research", "chemistry", "biology", "physics"],
}

_COST_THRESHOLDS = {
    "cheap": 2.0,
    "medium": 10.0,
}

_LATENCY_PATTERNS: dict[str, re.Pattern[str]] = {
    "fast": re.compile(r"\b(?:flash|lite|mini|fast|turbo|instant)\b", re.IGNORECASE),
    "slow": re.compile(r"\b(?:thinking|deepseek-r1)\b", re.IGNORECASE),
}

_META_ROUTER_IDS: frozenset[str] = frozenset({"openrouter/auto"})


def _is_excluded_entry(entry: dict[str, Any]) -> bool:
    """True for catalog entries that should not be profiled.

    Excluded categories:
    - Meta-routers (openrouter/auto): aggregate routing, not real models
    - Negative-cost sentinels: OpenRouter variable/aggregate pricing marker
    - Free-tier variants (:free suffix): unreliable structured output support,
      aggressive rate limits, and degraded quality
    """
    model_id: str = entry.get("id", "")
    if model_id in _META_ROUTER_IDS:
        return True
    if model_id.endswith(":free"):
        return True
    completion_cost: float = entry.get("completion_cost", 0.0)
    return completion_cost < -1.0


def derive_from_cloud_entry(entry: dict[str, Any]) -> IntelligenceProfile:
    """Derive an IntelligenceProfile from an enriched browser catalog entry."""
    if _is_excluded_entry(entry):
        raise ValueError(f"Excluded entry {entry.get('id', '')} must not be profiled")
    model_id: str = entry.get("id", "")
    basename = model_id.split("/", 1)[-1] if "/" in model_id else model_id

    domains = _derive_domains(entry)
    tasks = _derive_tasks(entry, domains)

    completion_cost: float = entry.get("completion_cost", 0.0)
    supported_params: list[str] = entry.get("supported_parameters") or []
    top_provider: dict[str, Any] = entry.get("top_provider") or {}

    return IntelligenceProfile(
        basename=basename,
        full_model_id=model_id,
        domains=domains,
        tasks=tasks,
        tool_usage_skill=_derive_tool_skill(supported_params),
        cost_bucket=_derive_cost_bucket(completion_cost),
        latency_bucket=_derive_latency_bucket(model_id),
        reasoning_depth=_derive_reasoning_depth(model_id, entry),
        completion_cost=completion_cost,
        context_length=entry.get("context_length", 0),
        max_completion_tokens=top_provider.get("max_completion_tokens"),
        source="cloud",
    )


def derive_bulk(entries: list[dict[str, Any]]) -> dict[str, IntelligenceProfile]:
    """Derive profiles for all entries. Returns {model_id: profile}."""
    profiles: dict[str, IntelligenceProfile] = {}
    for entry in entries:
        model_id = entry.get("id", "")
        if not model_id:
            continue
        if _is_excluded_entry(entry):
            continue
        profiles[model_id] = derive_from_cloud_entry(entry)
    return profiles


def _derive_domains(entry: dict[str, Any]) -> dict[str, DomainScore]:
    """Infer domain suitability from description keywords and tags."""
    description: str = (entry.get("description") or "").lower()
    model_id: str = entry.get("id", "").lower()
    tags: list[str] = entry.get("tags") or []
    combined = f"{description} {model_id}"

    domains: dict[str, DomainScore] = {}

    for domain, keywords in _DOMAIN_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            domains[domain] = DomainScore(
                score="good",
                evidence=[
                    Evidence(source="curated", detail="description keyword match")
                ],
            )

    if "code" in tags and "code" not in domains:
        domains["code"] = DomainScore(
            score="good",
            evidence=[Evidence(source="curated", detail="tagged as code model")],
        )

    if "reasoning" in tags and "reasoning" not in domains:
        domains["reasoning"] = DomainScore(
            score="good",
            evidence=[Evidence(source="curated", detail="tagged as reasoning model")],
        )

    return domains


def _derive_tasks(
    entry: dict[str, Any],
    domains: dict[str, DomainScore],
) -> dict[str, DomainScore]:
    """Map domains to pipeline/consult task scores.

    A model good at 'code' is likely good at 'code_review'.
    """
    tasks: dict[str, DomainScore] = {}
    tier: int = entry.get("tier", 0)
    all_tasks: tuple[str, ...] = (
        "code_review",
        "analysis",
        "summarization",
        "code_architecture",
        "planning",
        "research",
    )

    if "code" in domains:
        tasks["code_review"] = DomainScore(
            score=domains["code"].score,
            evidence=[Evidence(source="curated", detail="inferred from code domain")],
        )

    if "reasoning" in domains:
        tasks["analysis"] = DomainScore(
            score=domains["reasoning"].score,
            evidence=[
                Evidence(source="curated", detail="inferred from reasoning domain")
            ],
        )

    # code_architecture requires both coding and reasoning capability.
    if "code" in domains and "reasoning" in domains:
        tasks["code_architecture"] = DomainScore(
            score="good",
            evidence=[
                Evidence(
                    source="curated",
                    detail="inferred from code + reasoning domains",
                )
            ],
        )
    elif "code" in domains:
        tasks["code_architecture"] = DomainScore(
            score="neutral",
            evidence=[
                Evidence(source="curated", detail="inferred from code domain only"),
            ],
        )

    # planning is primarily reasoning-driven; code-only is a weaker signal.
    if "reasoning" in domains:
        tasks["planning"] = DomainScore(
            score="good",
            evidence=[
                Evidence(source="curated", detail="inferred from reasoning domain")
            ],
        )
    elif "code" in domains:
        tasks["planning"] = DomainScore(
            score="neutral",
            evidence=[Evidence(source="curated", detail="inferred from code domain")],
        )

    # research suitability can come from science, reasoning, or summarization domains.
    research_signals: list[str] = [
        domain
        for domain in ("science", "reasoning", "summarization")
        if domain in domains
    ]
    if research_signals:
        best_score: str = (
            "good" if "science" in domains or "reasoning" in domains else "neutral"
        )
        tasks["research"] = DomainScore(
            score=best_score,
            evidence=[
                Evidence(
                    source="curated",
                    detail=f"inferred from {', '.join(research_signals)} domains",
                ),
            ],
        )

    if tier >= 3:
        for task_name in all_tasks:
            if task_name not in tasks:
                tasks[task_name] = DomainScore(
                    score="good",
                    evidence=[Evidence(source="curated", detail=f"tier {tier} model")],
                )

    if tier >= 2:
        for task_name in all_tasks:
            if task_name not in tasks:
                tasks[task_name] = DomainScore(
                    score="neutral",
                    evidence=[Evidence(source="curated", detail=f"tier {tier} model")],
                )

    return tasks


def _derive_tool_skill(supported_params: list[str]) -> str | None:
    """Derive tool_usage_skill from supported_parameters."""
    if "tools" in supported_params:
        return "good"
    if "tool_choice" in supported_params:
        return "good"
    return None


def _derive_cost_bucket(
    completion_cost: float,
) -> str | None:
    """Classify cost bucket from per-million-token completion cost."""
    if completion_cost <= 0:
        return None
    if completion_cost < _COST_THRESHOLDS["cheap"]:
        return "cheap"
    if completion_cost < _COST_THRESHOLDS["medium"]:
        return "medium"
    return "expensive"


def _derive_latency_bucket(model_id: str) -> str | None:
    """Heuristic latency bucket from model ID patterns."""
    for bucket, pattern in _LATENCY_PATTERNS.items():
        if pattern.search(model_id):
            return bucket
    return None


def _derive_reasoning_depth(model_id: str, entry: dict[str, Any]) -> str | None:
    """Infer reasoning depth from model ID and tags."""
    tags: list[str] = entry.get("tags") or []
    if "reasoning" in tags:
        return "strong"
    if re.search(r"\b(?:pro|expert)\b", model_id, re.IGNORECASE):
        return "good"
    return None
