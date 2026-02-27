"""RAG-augmented question answering via local models.

Sends a free-form question with RAG-retrieved research context to local
models via Stargate. Unlike ``consult`` (which requires a pipeline step
fixture), ``ask`` takes any question and optionally enriches it with
research findings before forwarding to one or more models.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from .models import ConsultResult

DEFAULT_STARGATE_URL = "http://localhost:9999"

DEFAULT_ASK_MODELS: list[str] = [
    "qwen3-32b-awq-32768",
    "gpt-oss-20b-mxfp4-65536",
]

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
        resp = client.post(url, json=body)
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
