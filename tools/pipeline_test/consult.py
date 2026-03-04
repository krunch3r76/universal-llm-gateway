"""Consultation service: query other models for prompt improvement suggestions.

Sends pipeline step context (prompt + output + problem description) to
consultant models via Stargate, returning their analysis.  Default mode
is chained (sequential, each model reviews the prior model's output);
parallel mode is available for independent perspectives.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx
from transport_utils.rag_client import DEFAULT_RAG_URL, make_sync_client

from .http_helpers import PIPELINE_TEST_HEADERS, PIPELINE_TEST_PARAMS
from .models import ConsultResult, ModelCall, StepSnapshot

DEFAULT_STARGATE_URL = "http://localhost:9999"
DEFAULT_RAG_TOP_K = 5
DEFAULT_RAG_TIMEOUT = 10.0
DEFAULT_RAG_RECENCY_WEIGHT = 0.1
DEFAULT_RAG_CORPUS_DIR = Path("docs/research/prompting")
DEFAULT_RAG_PIPELINE_ID = "rag-context"
DEFAULT_RAG_PIPELINE_TIMEOUT = 70.0  # rag-context timeout_seconds: 60; allow margin
DEFAULT_RAG_RRF_K = 20

_RAG_DEFAULTS_PATH = Path(__file__).resolve().parent / "rag_defaults.yaml"


def default_rag_source_prefixes() -> list[Path]:
    """Default RAG source paths for ask/consult from config or fallback.

    Reads tools/pipeline_test/rag_defaults.yaml when present; key
    default_rag_source_prefixes (list of paths). Paths are resolved relative
    to CWD. If config missing or invalid, returns [DEFAULT_RAG_CORPUS_DIR].
    """
    if not _RAG_DEFAULTS_PATH.is_file():
        return [DEFAULT_RAG_CORPUS_DIR]
    try:
        import yaml

        raw = _RAG_DEFAULTS_PATH.read_text()
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            return [DEFAULT_RAG_CORPUS_DIR]
        prefixes = data.get("default_rag_source_prefixes")
        if not isinstance(prefixes, list) or not prefixes:
            return [DEFAULT_RAG_CORPUS_DIR]
        out: list[Path] = []
        for p in prefixes:
            if isinstance(p, str) and p.strip():
                out.append(Path(p.strip()).expanduser().resolve())
        return out if out else [DEFAULT_RAG_CORPUS_DIR]
    except Exception:  # noqa: BLE001
        return [DEFAULT_RAG_CORPUS_DIR]


DEFAULT_CONSULTANTS: list[str] = [
    "gpt-oss-20b-mxfp4-65536",
    "google/gemini-2.5-flash",
]


def order_for_chain(models: list[str]) -> list[str]:
    """Order models for chaining: local first (analyst), cloud second (reviewer).

    Local models (no '/' in ID) have lower context and are typically lower-tier,
    making them natural analysts. Cloud models review and augment.
    Preserves relative order within each group.
    """
    local = [m for m in models if "/" not in m]
    cloud = [m for m in models if "/" in m]
    return local + cloud

_CONSULT_SELECT_TAGS: list[str] = ["code", "reasoning"]
_CONSULT_SELECT_MIN_CONTEXT: int = 32768


def resolve_consultant_models(count: int = 2) -> list[str]:
    """Pick consultant models via cloud proxy, falling back to defaults."""
    from .cloud_select import select_models

    selected = select_models(
        tags=_CONSULT_SELECT_TAGS,
        min_context=_CONSULT_SELECT_MIN_CONTEXT,
        count=count,
    )
    return selected or DEFAULT_CONSULTANTS


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
    if not consultant_models:
        return []
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


def chain_step(
    step: StepSnapshot,
    problem: str,
    call_label: str | None = None,
    models: list[str] | None = None,
    rag_findings: list[str] | None = None,
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout: float = 300.0,
    output_limit_chars: int | None = None,
    on_result: Callable[[ConsultResult, int, int], None] | None = None,
) -> list[ConsultResult]:
    """Query consultant models sequentially, each reviewing prior output.

    Model 1 receives the standard consultation prompt. Model 2+ receives
    the same prompt augmented with all prior models' responses and a
    reviewer directive.
    """
    consultant_models = models or DEFAULT_CONSULTANTS
    base_prompt = _build_user_prompt(
        step=step,
        call_label=call_label,
        problem=problem,
        rag_findings=rag_findings,
        output_limit_chars=output_limit_chars,
    )

    results: list[ConsultResult] = []
    for idx, mid in enumerate(consultant_models):
        prompt = _augment_with_prior(base_prompt, results) if results else base_prompt
        try:
            result = _query_consultant(
                model_id=mid,
                user_prompt=prompt,
                stargate_url=stargate_url,
                timeout=timeout,
            )
        except Exception as exc:
            result = ConsultResult(model_id=mid, error=str(exc))
        results.append(result)
        if on_result:
            on_result(result, idx, len(consultant_models))
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
        len(_SYSTEM_PROMPT)
        + len(f"## Pipeline Step: {step.step_name} ({step.step_type})")
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


def fetch_rag_findings(
    problem: str,
    *,
    rag_url: str = DEFAULT_RAG_URL,
    top_k: int = DEFAULT_RAG_TOP_K,
    timeout: float = DEFAULT_RAG_TIMEOUT,
    source_prefixes: list[str] | None = None,
    scope: str | None = None,
    recency_weight: float = DEFAULT_RAG_RECENCY_WEIGHT,
) -> tuple[list[str], str | None]:
    """Search RAG for relevant prompt-engineering chunks.

    Returns findings and optional error text. Errors are non-fatal so consult can
    continue without RAG context.
    """
    body: dict[str, object] = {
        "query": problem,
        "top_k": top_k,
        "recency_weight": recency_weight,
    }
    if source_prefixes:
        body["source_prefixes"] = source_prefixes
    elif scope:
        body["scope"] = scope

    try:
        with make_sync_client(rag_url, timeout=timeout) as client:
            response = client.post("/search", json=body)
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


def fetch_rag_via_pipeline(
    query: str,
    *,
    pipeline_id: str = DEFAULT_RAG_PIPELINE_ID,
    stargate_url: str = DEFAULT_STARGATE_URL,
    rag_url: str = DEFAULT_RAG_URL,
    timeout: float = DEFAULT_RAG_PIPELINE_TIMEOUT,
    pipeline_options: dict[str, Any] | None = None,
) -> tuple[list[str], str | None]:
    """Use an intelligent RAG pipeline for context retrieval.

    Calls a retrieval pipeline (default: ``rag-context``) which rewrites the
    query into embedding-optimized sub-queries, executes parallel RAG searches,
    and returns assembled context via RRF merge — as a single formatted block.

    Returns the same ``(findings, error)`` shape as ``fetch_rag_findings`` so
    callers can treat both paths identically.

    Invariant: ∀ non-empty response: len(findings) == 1 (assembled context block)
    """
    from pipelines.rag.scope_helpers import fetch_scope_options_text

    url = f"{stargate_url.rstrip('/')}/v1/chat/completions"
    base_options: dict[str, Any] = pipeline_options or {}
    base_options.setdefault("scope_options", fetch_scope_options_text(rag_url=rag_url))
    body: dict[str, Any] = {
        "model": pipeline_id,
        "messages": [{"role": "user", "content": query}],
        "stream": False,
        "pipeline_options": base_options,
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                url,
                json=body,
                params=PIPELINE_TEST_PARAMS,
                headers=PIPELINE_TEST_HEADERS,
            )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)

    data = resp.json()
    content: str = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content.strip():
        return [], f"Pipeline '{pipeline_id}' returned empty context"
    return [content], None


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
