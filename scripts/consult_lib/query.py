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


def _select_fallback_model(
    timed_out_model: str,
    role: str,
    stargate_url: str,
) -> str | None:
    """Query the unified select endpoint for an alternative cloud model.

    Excludes timed_out_model via avoid_models so the endpoint never returns
    the same model that already failed.
    """
    from .model_selection import load_roles, split_role_config

    _, role_requirements = split_role_config(load_roles())
    req_config = role_requirements.get(role)
    if req_config is None:
        return None

    payload = {**req_config, "count": 4, "avoid_models": [timed_out_model]}

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{stargate_url.rstrip('/')}/v1/models/select",
                json=payload,
            )
        resp.raise_for_status()
        models = [
            m["id"]
            for m in resp.json().get("models", [])
            if isinstance(m, dict) and m.get("id") and "/" in m["id"]
        ]
        return models[0] if models else None
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "Failed to select fallback model for role '%s': %s", role, e
        )
        return None


def query_chain(
    models: list[str],
    system_prompt: str,
    user_prompt: str,
    stargate_url: str,
    timeout: float,
    chain_directive: str | None = None,
    *,
    role: str | None = None,
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
            is_timeout = elapsed >= timeout * 0.95
            error_msg = str(exc)

            if is_timeout:
                error_msg = f"timeout after {elapsed:.0f}s: {error_msg}"

                if role is not None:
                    fallback_model = _select_fallback_model(mid, role, stargate_url)
                    if fallback_model:
                        print(
                            f"  Phase {idx + 1} ({phase}): timed out after "
                            f"{elapsed:.0f}s, trying fallback...",
                            file=sys.stderr,
                        )
                        print(
                            f"  Phase {idx + 1} ({phase} fallback): "
                            f"{fallback_model} (timeout={timeout:.0f}s)...",
                            file=sys.stderr,
                        )
                        try:
                            result = query_model(
                                fallback_model,
                                system_prompt,
                                prompt,
                                stargate_url,
                                timeout,
                            )
                            result["phase"] = phase
                            result["fallback_for"] = mid
                            results.append(result)
                            continue
                        except Exception as fallback_exc:
                            fb_elapsed = time.monotonic() - start
                            print(
                                f"  Phase {idx + 1} ({phase} fallback): "
                                f"also failed ({fb_elapsed:.0f}s)",
                                file=sys.stderr,
                            )
                            error_msg = (
                                f"timeout after {elapsed:.0f}s (original: {mid}); "
                                f"fallback {fallback_model} also failed: "
                                f"{fallback_exc}"
                            )
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
