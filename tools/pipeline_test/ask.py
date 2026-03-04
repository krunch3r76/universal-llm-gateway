"""RAG-augmented question answering via local models.

Sends a free-form question with RAG-retrieved research context to local
models via Stargate. Unlike ``consult`` (which requires a pipeline step
fixture), ``ask`` takes any question and optionally enriches it with
research findings before forwarding to one or more models.  Supports
chained mode where models run sequentially, each reviewing the prior
model's output.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from .http_helpers import PIPELINE_TEST_HEADERS, PIPELINE_TEST_PARAMS
from .models import ConsultResult

DEFAULT_STARGATE_URL = "http://localhost:9999"

DEFAULT_ASK_MODELS: list[str] = [
    "qwen3-32b-awq-32768",
    "gpt-oss-20b-mxfp4-65536",
]

_ASK_SELECT_TAGS: list[str] = ["general", "reasoning"]
_ASK_SELECT_EXCLUDE_TAGS: list[str] = ["fast"]
_ASK_SELECT_MIN_CONTEXT: int = 65536


def resolve_ask_models(count: int = 2) -> list[str]:
    """Pick ask models via cloud proxy, falling back to defaults."""
    from .cloud_select import select_models

    selected = select_models(
        tags=_ASK_SELECT_TAGS,
        exclude_tags=_ASK_SELECT_EXCLUDE_TAGS,
        min_context=_ASK_SELECT_MIN_CONTEXT,
        count=count,
    )
    return selected or DEFAULT_ASK_MODELS


_SYSTEM_PROMPT = """\
You are an AI assistant with deep expertise in LLM prompt engineering, \
multi-model pipelines, and small/open model optimization. Research findings \
from published papers are provided when available.

Rules:
- Ground your response in the provided research when applicable
- Cite specific techniques or findings by source
- Be practical: suggest actionable approaches
- If the research doesn't cover the question, say so and provide your best guidance
- Structure complex answers with clear sections"""


def estimate_fixed_chars(question: str) -> int:
    """Return the char count of everything except RAG findings."""
    return len(_SYSTEM_PROMPT) + len(f"## Question\n{question}") + 50


def ask_models(
    question: str,
    *,
    models: list[str] | None = None,
    rag_findings: list[str] | None = None,
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout: float = 300.0,
) -> list[ConsultResult]:
    """Send a free-form question (optionally RAG-augmented) to local models."""
    target_models = models or DEFAULT_ASK_MODELS
    if not target_models:
        return []
    user_prompt = _build_prompt(question, rag_findings)

    results: list[ConsultResult] = []
    with ThreadPoolExecutor(max_workers=len(target_models)) as pool:
        futures = {
            pool.submit(
                _query_model,
                model_id=mid,
                user_prompt=user_prompt,
                stargate_url=stargate_url,
                timeout=timeout,
            ): mid
            for mid in target_models
        }
        for future in as_completed(futures):
            mid = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(ConsultResult(model_id=mid, error=str(exc)))

    model_order = {m: i for i, m in enumerate(target_models)}
    results.sort(key=lambda r: model_order.get(r.model_id, 999))
    return results


def chain_ask(
    question: str,
    *,
    models: list[str] | None = None,
    rag_findings: list[str] | None = None,
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout: float = 300.0,
    on_result: Callable[[ConsultResult, int, int], None] | None = None,
) -> list[ConsultResult]:
    """Send a question to models sequentially, each reviewing prior output.

    Model 1 answers the question directly. Model 2+ receives the same
    prompt augmented with all prior models' responses and a reviewer
    directive.
    """
    target_models = models or DEFAULT_ASK_MODELS
    base_prompt = _build_prompt(question, rag_findings)

    results: list[ConsultResult] = []
    for idx, mid in enumerate(target_models):
        prompt = _augment_with_prior(base_prompt, results) if results else base_prompt
        try:
            result = _query_model(
                model_id=mid,
                user_prompt=prompt,
                stargate_url=stargate_url,
                timeout=timeout,
            )
        except Exception as exc:
            result = ConsultResult(model_id=mid, error=str(exc))
        results.append(result)
        if on_result:
            on_result(result, idx, len(target_models))
    return results


def _augment_with_prior(
    base_prompt: str,
    prior_results: list[ConsultResult],
) -> str:
    """Append prior models' analyses and a reviewer directive to the prompt."""
    sections: list[str] = [base_prompt]
    for result in prior_results:
        if result.error or not result.response_text:
            continue
        sections.append(
            f"## Prior Analysis (by {result.model_id})\n{result.response_text}"
        )
    sections.append(
        "## Your Role\n"
        "You are a reviewer in a chained consultation. The analysis above was "
        "produced by a prior model. Evaluate whether you agree with its "
        "recommendations, identify any gaps or risks it missed, and propose "
        "additional changes if warranted. Do not re-derive what the prior "
        "analysis already covers correctly — focus on validation and augmentation."
    )
    return "\n\n".join(sections)


def _build_prompt(
    question: str,
    rag_findings: list[str] | None = None,
) -> str:
    """Build user prompt with optional RAG context."""
    sections: list[str] = []

    if rag_findings:
        sections.append(
            "## Relevant Research\n"
            + "\n\n".join(
                f"### Finding {idx}\n{finding}"
                for idx, finding in enumerate(rag_findings, start=1)
            )
        )

    sections.append(f"## Question\n{question}")
    return "\n\n".join(sections)


def _query_model(
    model_id: str,
    user_prompt: str,
    stargate_url: str,
    timeout: float,
) -> ConsultResult:
    """Send a question to a single model via Stargate."""
    url = f"{stargate_url.rstrip('/')}/v1/chat/completions"
    body: dict[str, Any] = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "temperature": 0.4,
    }

    start = time.monotonic()
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(
            url, json=body, params=PIPELINE_TEST_PARAMS, headers=PIPELINE_TEST_HEADERS
        )
    elapsed_ms = (time.monotonic() - start) * 1000

    resp.raise_for_status()
    data = resp.json()

    choice = data.get("choices", [{}])[0]
    message = choice.get("message", {})
    usage = data.get("usage", {})

    return ConsultResult(
        model_id=data.get("model", model_id),
        response_text=message.get("content", ""),
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        latency_ms=elapsed_ms,
    )
