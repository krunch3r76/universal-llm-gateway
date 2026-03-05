"""Model query execution: single, parallel, and chained modes."""

from __future__ import annotations

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from .constants import DEFAULT_CHAIN_DIRECTIVE, MAX_PRIOR_CHARS


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
) -> dict[str, Any]:
    """Send consultation to a single model."""
    url = f"{stargate_url.rstrip('/')}/v1/chat/completions"
    body: dict[str, Any] = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "temperature": 0.4,
    }
    start = time.monotonic()
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body, params={"disable_profile": "true"})
    elapsed_ms = (time.monotonic() - start) * 1000
    resp.raise_for_status()
    data = resp.json()
    choice = data.get("choices", [{}])[0]
    usage = data.get("usage", {})
    raw_response = choice.get("message", {}).get("content", "")
    return {
        "model_id": data.get("model", model_id),
        "response": strip_think_blocks(raw_response),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "latency_ms": round(elapsed_ms),
    }


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
) -> list[dict[str, Any]]:
    """Query models sequentially, each reviewing prior output.

    Prior responses are capped at MAX_PRIOR_CHARS to prevent unbounded
    prompt growth across chain phases.
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
            f"  Phase {idx + 1} ({phase}): {mid} (timeout={timeout:.0f}s)...",
            file=sys.stderr,
        )
        start = time.monotonic()
        try:
            result = query_model(mid, system_prompt, prompt, stargate_url, timeout)
            result["phase"] = phase
        except Exception as exc:
            elapsed = time.monotonic() - start
            error_msg = str(exc)
            if elapsed >= timeout * 0.95:
                error_msg = f"timeout after {elapsed:.0f}s: {error_msg}"
            elif "connect" in error_msg.lower() or "connection" in error_msg.lower():
                error_msg = f"connection error: {error_msg}"
            result = {"model_id": mid, "error": error_msg, "phase": phase}
        results.append(result)
    return results


def query_parallel(
    models: list[str],
    system_prompt: str,
    user_prompt: str,
    stargate_url: str,
    timeout: float,
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
