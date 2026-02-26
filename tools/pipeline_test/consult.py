"""Consultation service: query other models for prompt improvement suggestions.

Sends pipeline step context (prompt + output + problem description) to
consultant models via Stargate in parallel, returning their analysis.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx

from .models import ConsultResult, ModelCall, StepSnapshot

DEFAULT_STARGATE_URL = "http://localhost:9999"
DEFAULT_RAG_URL = "http://localhost:8100"
DEFAULT_RAG_TOP_K = 5
DEFAULT_RAG_TIMEOUT = 10.0
DEFAULT_RAG_RECENCY_WEIGHT = 0.2
DEFAULT_RAG_CORPUS_DIR = Path("docs/research/prompting")

DEFAULT_CONSULTANTS: list[str] = [
    "qwen3-32b-awq-32768",
    "gpt-oss-20b-mxfp4-65536",
]

_SYSTEM_PROMPT = """\
You are a prompt engineer evaluating a pipeline step executed by a smaller LLM. \
Analyze the prompt-output pair below and suggest concrete improvements.

Rules:
- Be specific: quote exact text to change, add, or remove in the prompts
- Focus on the described problem — do not enumerate every possible improvement
- Suggest minimal edits (surgical changes over full rewrites)
- If the prompt is fine and the issue is model capability, say so
- Structure your response: Issues Found → Suggested Changes → Rationale"""

_TASK_INSTRUCTIONS = (
    "## Your Task\n"
    "1. What specific issues do you see in the output given the problem description?\n"
    "2. What changes to the system prompt and/or user prompt would fix them?\n"
    "3. Provide the exact revised prompt text for each change you recommend."
)


def consult_step(
    step: StepSnapshot,
    problem: str,
    call_label: str | None = None,
    models: list[str] | None = None,
    rag_findings: list[str] | None = None,
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout: float = 300.0,
    output_limit_chars: int | None = None,
) -> list[ConsultResult]:
    """Query consultant models about a pipeline step's prompt quality.

    Sends the step's prompt context and output to each consultant model
    in parallel, along with a problem description.
    """
    consultant_models = models or DEFAULT_CONSULTANTS
    user_prompt = _build_user_prompt(
        step=step,
        call_label=call_label,
        problem=problem,
        rag_findings=rag_findings,
        output_limit_chars=output_limit_chars,
    )

    results: list[ConsultResult] = []
    with ThreadPoolExecutor(max_workers=len(consultant_models)) as pool:
        futures = {
            pool.submit(
                _query_consultant,
                model_id=mid,
                user_prompt=user_prompt,
                stargate_url=stargate_url,
                timeout=timeout,
            ): mid
            for mid in consultant_models
        }
        for future in as_completed(futures):
            mid = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(ConsultResult(model_id=mid, error=str(exc)))

    model_order = {m: i for i, m in enumerate(consultant_models)}
    results.sort(key=lambda r: model_order.get(r.model_id, 999))
    return results


def estimate_fixed_chars(
    step: StepSnapshot,
    call_label: str | None,
    problem: str,
) -> tuple[int, int]:
    """Return *(fixed_chars, output_chars)* for budget computation.

    *fixed_chars* covers everything except model output and RAG findings.
    """
    call = _select_call(step, call_label)
    fixed = (
        len(f"## Pipeline Step: {step.step_name} ({step.step_type})")
        + len(f"## Problem\n{problem}")
        + len(f"## System Prompt Given to Model\n{call.system_prompt or ''}")
        + len(f"## User Prompt Given to Model\n{call.user_prompt}")
        + len(_TASK_INSTRUCTIONS)
        + 100  # section separators, newlines
    )
    return fixed, len(call.response_text)


def _build_user_prompt(
    step: StepSnapshot,
    call_label: str | None,
    problem: str,
    rag_findings: list[str] | None = None,
    output_limit_chars: int | None = None,
) -> str:
    """Package step context into a consultation prompt."""
    call = _select_call(step, call_label)

    sections: list[str] = [
        f"## Pipeline Step: {step.step_name} ({step.step_type})",
        f"## Problem\n{problem}",
    ]

    if call.system_prompt:
        sections.append(f"## System Prompt Given to Model\n{call.system_prompt}")

    sections.append(f"## User Prompt Given to Model\n{call.user_prompt}")

    output = call.response_text
    limit = output_limit_chars if output_limit_chars is not None else len(output)
    if len(output) > limit:
        output = (
            output[:limit]
            + f"\n\n[... truncated at {limit} of {len(call.response_text)} chars]"
        )
    sections.append(f"## Model Output\n{output}")

    if rag_findings:
        sections.append(
            "## Relevant Research Findings\n"
            + "\n\n".join(
                f"### Finding {idx}\n{finding}"
                for idx, finding in enumerate(rag_findings, start=1)
            )
        )

    sections.append(_TASK_INSTRUCTIONS)

    return "\n\n".join(sections)


def _query_consultant(
    model_id: str,
    user_prompt: str,
    stargate_url: str,
    timeout: float,
) -> ConsultResult:
    """Send a single consultation request to Stargate."""
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


def fetch_rag_findings(
    problem: str,
    *,
    rag_url: str = DEFAULT_RAG_URL,
    top_k: int = DEFAULT_RAG_TOP_K,
    timeout: float = DEFAULT_RAG_TIMEOUT,
    source_prefixes: list[str] | None = None,
    recency_weight: float = DEFAULT_RAG_RECENCY_WEIGHT,
) -> tuple[list[str], str | None]:
    """Search RAG for relevant prompt-engineering chunks.

    Returns findings and optional error text. Errors are non-fatal so consult can
    continue without RAG context.
    """
    url = f"{rag_url.rstrip('/')}/search"
    body: dict[str, object] = {
        "query": problem,
        "top_k": top_k,
        "recency_weight": recency_weight,
    }
    if source_prefixes:
        body["source_prefixes"] = source_prefixes

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=body)
        _ = response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)

    raw_data = response.json()
    if not isinstance(raw_data, dict):
        return [], "RAG /search response was not a JSON object"
    data: dict[str, object] = raw_data

    chunks_raw = data.get("chunks", [])
    metadata_raw = data.get("metadata", [])
    distances_raw = data.get("distances", [])
    chunks = chunks_raw if isinstance(chunks_raw, list) else []
    metadata = metadata_raw if isinstance(metadata_raw, list) else []
    distances = distances_raw if isinstance(distances_raw, list) else []
    if not isinstance(chunks, list):
        return [], "RAG /search response did not include a chunk list"

    findings: list[str] = []
    for idx, chunk in enumerate(chunks):
        if not isinstance(chunk, str):
            continue
        meta = (
            metadata[idx]
            if idx < len(metadata) and isinstance(metadata[idx], dict)
            else {}
        )
        distance = (
            distances[idx]
            if idx < len(distances) and isinstance(distances[idx], int | float)
            else None
        )
        source = (
            meta.get("source", "unknown-source")
            if isinstance(meta, dict)
            else "unknown-source"
        )
        chunk_index = (
            meta.get("chunk_index", "n/a") if isinstance(meta, dict) else "n/a"
        )
        header = f"Source: {source} (chunk={chunk_index}"
        if distance is not None:
            header += f", cosine_distance={distance:.4f}"
        header += ")"
        findings.append(f"{header}\n{chunk}")

    return findings, None


def _select_call(step: StepSnapshot, call_label: str | None) -> ModelCall:
    """Find a call by label, or return the first."""
    if not step.model_calls:
        raise ValueError(f"Step '{step.step_name}' has no model calls")
    if call_label:
        for call in step.model_calls:
            if call.call_label == call_label:
                return call
        labels = [c.call_label for c in step.model_calls]
        raise KeyError(f"Call '{call_label}' not found. Available: {labels}")
    return step.model_calls[0]
