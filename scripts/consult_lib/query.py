"""Model query execution: single, parallel, and chained modes."""

from __future__ import annotations

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from .constants import DEFAULT_CHAIN_DIRECTIVE, MAX_PRIOR_CHARS
from .progress import ProgressAbortError, post_with_progress
from .readiness import resolve_ready_model


def build_prompt(
    question: str,
    rag_findings: list[str] | None = None,
    file_context: list[str] | None = None,
) -> str:
    """Assemble the user prompt from question + context."""
    sections: list[str] = []
    if file_context:
        sections.append("## Code/Documentation Context\n" + "\n\n".join(file_context))
    if rag_findings:
        sections.append(
            "## Retrieved Knowledge\n"
            + "\n\n".join(
                f"### Finding {i}\n{f}" for i, f in enumerate(rag_findings, 1)
            )
        )
    sections.append(f"## Question\n{question}")
    return "\n\n".join(sections)


def strip_think_blocks(text: str) -> str:
    """Remove all <think>...</think> blocks from model output.

    Handles leading blocks (reasoning models like Qwen3) and any embedded
    blocks. Applied before chaining so thinking tokens never contaminate
    the next model's context. Centralisation target: stargate-level stripping.
    """
    return re.sub(
        r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE
    ).strip()


def query_model(
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    stargate_url: str,
    timeout: float,
    *,
    require_warm: bool = False,
    fallback_models: list[str] | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Send consultation to a single model.

    When ``require_warm`` is True, probes
    ``GET /v1/models/{id}?include_status=true`` first and refuses
    cold/loading seats (trying ``fallback_models`` if provided) so
    latency-sensitive one-shots do not hang on GGUF cold-load.

    ``timeout`` is the no-progress step budget. ``deadline`` is the hard
    wall-clock ceiling (defaults via progress.derive_deadline).
    """
    ready = resolve_ready_model(
        model_id,
        stargate_url,
        require_warm=require_warm,
        fallback_models=fallback_models,
    )
    selected = ready["model_id"]
    url = f"{stargate_url.rstrip('/')}/v1/chat/completions"
    body: dict[str, Any] = {
        "model": selected,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "temperature": 0.4,
    }
    start = time.monotonic()
    resp = post_with_progress(
        url,
        body,
        model_id=selected,
        stargate_url=stargate_url,
        step_budget=timeout,
        deadline=deadline,
        params={"disable_profile": "true"},
    )
    elapsed_ms = (time.monotonic() - start) * 1000
    resp.raise_for_status()
    data = resp.json()
    choice = data.get("choices", [{}])[0]
    usage = data.get("usage", {})
    raw_response = choice.get("message", {}).get("content", "")
    result: dict[str, Any] = {
        "model_id": data.get("model", selected),
        "response": strip_think_blocks(raw_response),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "latency_ms": round(elapsed_ms),
    }
    if selected != model_id:
        result["requested_model_id"] = model_id
        result["fallback_used"] = True
    if ready.get("status"):
        result["preflight_status"] = ready["status"]
    return result


def augment_with_prior(
    base_prompt: str,
    prior_results: list[dict[str, Any]],
    directive: str | None = None,
) -> str:
    """Append prior models' analyses and a reviewer directive to the prompt."""
    sections: list[str] = [base_prompt]
    for result in prior_results:
        if "error" in result or not result.get("response"):
            continue
        sections.append(
            f"## Prior Analysis (by {result['model_id']})\n{result['response']}"
        )
    sections.append(f"## Your Role\n{directive or DEFAULT_CHAIN_DIRECTIVE}")
    return "\n\n".join(sections)


def query_chain(
    models: list[str],
    system_prompt: str,
    user_prompt: str,
    stargate_url: str,
    timeout: float,
    chain_directive: str | None = None,
    *,
    require_warm: bool = False,
    fallback_models: list[str] | None = None,
    deadline: float | None = None,
) -> list[dict[str, Any]]:
    """Query models sequentially, each reviewing prior output.

    Prior responses are capped at MAX_PRIOR_CHARS to prevent unbounded
    prompt growth across chain phases.

    Each result dict includes phase trace fields: ``phase``, ``phase_index``,
    ``started_at``, ``finished_at``, ``duration_ms``, ``input_prompt_preview``,
    and ``response_preview`` for later agent recovery.
    """
    results: list[dict[str, Any]] = []
    for idx, mid in enumerate(models):
        if results:
            capped: list[dict[str, Any]] = []
            for r in results:
                if "response" in r and len(r["response"]) > MAX_PRIOR_CHARS:
                    r = dict(r)
                    r["response"] = (
                        r["response"][:MAX_PRIOR_CHARS]
                        + f"\n[... truncated to {MAX_PRIOR_CHARS} chars]"
                    )
                capped.append(r)
            prompt = augment_with_prior(user_prompt, capped, chain_directive)
        else:
            prompt = user_prompt
        phase = "analyst" if idx == 0 else "reviewer"
        print(
            f"  Phase {idx + 1} ({phase}): {mid} "
            f"(step_budget={timeout:.0f}s)...",
            file=sys.stderr,
        )
        phase_started = time.time()
        call_start = time.monotonic()
        try:
            result = query_model(
                mid,
                system_prompt,
                prompt,
                stargate_url,
                timeout,
                require_warm=require_warm,
                fallback_models=fallback_models,
                deadline=deadline,
            )
            result["phase"] = phase
        except (httpx.TimeoutException, ProgressAbortError) as exc:
            elapsed = time.monotonic() - call_start
            error_msg = f"timeout after {elapsed:.0f}s: {exc}"
            result = {"model_id": mid, "error": error_msg, "phase": phase}
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            detail = exc.response.text[:200]
            error_msg = f"http {status}: {detail}"
            result = {"model_id": mid, "error": error_msg, "phase": phase}
        except httpx.RequestError as exc:
            error_msg = str(exc)
            if "connect" in error_msg.lower() or "connection" in error_msg.lower():
                error_msg = f"connection error: {error_msg}"
            else:
                error_msg = f"network error: {error_msg}"
            result = {"model_id": mid, "error": error_msg, "phase": phase}
        except RuntimeError as exc:
            error_msg = str(exc)
            result = {"model_id": mid, "error": error_msg, "phase": phase}
        except Exception as exc:
            error_msg = f"unexpected error: {exc}"
            result = {"model_id": mid, "error": error_msg, "phase": phase}
        phase_finished = time.time()
        result["phase_index"] = idx
        result["started_at"] = phase_started
        result["finished_at"] = phase_finished
        result["duration_ms"] = round((phase_finished - phase_started) * 1000, 1)
        result["input_prompt_preview"] = prompt[:500]
        result["response_preview"] = (
            result.get("response") or result.get("error") or ""
        )[:500]
        results.append(result)
    return results


def query_parallel(
    models: list[str],
    system_prompt: str,
    user_prompt: str,
    stargate_url: str,
    timeout: float,
    *,
    require_warm: bool = False,
    fallback_models: list[str] | None = None,
    deadline: float | None = None,
) -> list[dict[str, Any]]:
    """Query models in parallel, return results in original order."""
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(models)) as pool:
        futures = {
            pool.submit(
                query_model,
                model_id=mid,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                stargate_url=stargate_url,
                timeout=timeout,
                require_warm=require_warm,
                fallback_models=fallback_models,
                deadline=deadline,
            ): mid
            for mid in models
        }
        for future in as_completed(futures):
            mid = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"model_id": mid, "error": str(exc)})
    order = {m: i for i, m in enumerate(models)}
    results.sort(key=lambda r: order.get(r.get("model_id", ""), 999))
    return results
