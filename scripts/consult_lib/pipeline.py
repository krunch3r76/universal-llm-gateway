"""Pipeline consultation: generic and code-review pipeline execution."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx

from .constants import DEFAULT_STARGATE_URL

_PIPELINE_MAP: dict[str, str] = {
    "researcher": "consult-researcher",
    "architect": "consult-architect",
    "planner": "consult-planner",
    "prompt_engineer": "consult-prompt-engineer",
    "reviewer": "code-review",
    "modularizer": "modularize",
}


class PipelineError(RuntimeError):
    """Pipeline HTTP error with optional execution correlation."""

    def __init__(self, message: str, *, execution_id: str | None = None) -> None:
        super().__init__(message)
        self.execution_id = execution_id


def get_pipeline_id(role: str) -> str | None:
    """Map a consult role to its pipeline virtual model ID."""
    return _PIPELINE_MAP.get(role)


def query_pipeline(
    *,
    pipeline_id: str,
    user_message: str,
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout: float = 300.0,
    model_override: str | None = None,
    pipeline_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a single pipeline consultation via Stargate.

    Args:
        pipeline_id: Pipeline virtual model ID (e.g. "consult-researcher").
        user_message: Complete user message (question + context).
        stargate_url: Stargate base URL.
        timeout: Request timeout in seconds.
        model_override: Force a specific model via model_ref_overrides.
        pipeline_options: Additional pipeline options dict.

    Returns:
        Dict with model_id, response, prompt_tokens, completion_tokens, latency_ms.
    """
    import time

    body: dict[str, Any] = {
        "model": pipeline_id,
        "messages": [{"role": "user", "content": user_message}],
        "stream": False,
    }

    opts: dict[str, Any] = dict(pipeline_options or {})
    if model_override:
        overrides = opts.setdefault("model_ref_overrides", {})
        overrides["consult"] = model_override
        overrides["analyze"] = model_override
        overrides["review"] = model_override
    if opts:
        body["pipeline_options"] = opts

    start = time.monotonic()
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{stargate_url.rstrip('/')}/v1/chat/completions",
            json=body,
            params={"disable_profile": "true"},
        )
    elapsed_ms = (time.monotonic() - start) * 1000
    execution_id = response.headers.get("X-Pipeline-Execution-Id")

    if response.status_code >= 400:
        try:
            detail = response.json()
            if isinstance(detail, dict):
                err = detail.get("detail", detail)
                if isinstance(err, dict):
                    msg = err.get("error", {})
                    if isinstance(msg, dict):
                        msg = msg.get("message", "")
                    detail_str = str(msg) if msg else str(err)
                else:
                    detail_str = str(err)
            else:
                detail_str = str(detail)
        except Exception:
            detail_str = response.text[:200]
        raise PipelineError(
            f"Pipeline '{pipeline_id}' failed with HTTP {response.status_code}: {detail_str}",
            execution_id=execution_id,
        )
    data = response.json()

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    usage = data.get("usage", {}) if isinstance(data, dict) else {}
    used_model = data.get("model", pipeline_id)
    return {
        "model_id": used_model,
        "response": content,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "latency_ms": elapsed_ms,
        "execution_id": execution_id,
    }


def query_pipeline_multi(
    *,
    pipeline_id: str,
    user_message: str,
    models: list[str],
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout: float = 300.0,
    pipeline_options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute pipeline consultation for multiple models in parallel."""
    if models:
        print(
            f"Pipeline multi-model: invoking {pipeline_id} for {len(models)} model(s): "
            f"{', '.join(models)}",
            file=sys.stderr,
        )
    with ThreadPoolExecutor(max_workers=len(models)) as pool:
        futures = {
            pool.submit(
                query_pipeline,
                pipeline_id=pipeline_id,
                user_message=user_message,
                stargate_url=stargate_url,
                timeout=timeout,
                model_override=model,
                pipeline_options=pipeline_options,
            ): model
            for model in models
        }
        results: list[dict[str, Any]] = []
        for future in futures:
            requested_model = futures[future]
            try:
                result = future.result()
                # Stargate returns the pipeline virtual ID (e.g. consult-architect); preserve
                # the requested model so output shows which model produced each response.
                result["model_id"] = requested_model
                results.append(result)
            except Exception as exc:
                print(
                    f"Pipeline request failed for model {requested_model}: {exc}",
                    file=sys.stderr,
                )
                results.append(
                    {
                        "model_id": requested_model,
                        "error": str(exc),
                    }
                )
    return results


def fetch_pipeline_batches(
    stargate_url: str,
    files: list[str],
    *,
    pipeline: str = "code-review",
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch token-budgeted batch plan from Stargate estimator endpoint."""
    items = [{"name": path, "chars": Path(path).stat().st_size} for path in files]
    payload = {"pipeline": pipeline, "items": items}
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{stargate_url.rstrip('/')}/v1/pipelines/estimate",
            json=payload,
            params={"disable_profile": "true"},
        )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Estimator response is not a JSON object")
    return data


def query_code_review_batch(
    *,
    stargate_url: str,
    batch: dict[str, Any],
    timeout: float,
    pipeline_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a single code-review batch via pipeline model."""
    batch_files = [str(path) for path in batch.get("items", [])]
    parts: list[str] = []
    for file_path in batch_files:
        content = Path(file_path).read_text(errors="replace")
        parts.append(f"### {file_path}\n{content}")
    body: dict[str, Any] = {
        "model": "code-review",
        "messages": [{"role": "user", "content": "\n\n".join(parts)}],
        "stream": False,
    }
    if pipeline_options:
        body["pipeline_options"] = pipeline_options
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{stargate_url.rstrip('/')}/v1/chat/completions",
            json=body,
            params={"disable_profile": "true"},
        )
    execution_id = response.headers.get("X-Pipeline-Execution-Id")
    response.raise_for_status()
    data = response.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    try:
        parsed = json.loads(content) if isinstance(content, str) else content
    except Exception:
        parsed = content
    usage = data.get("usage", {}) if isinstance(data, dict) else {}
    return {
        "batch": batch,
        "result": parsed,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "execution_id": execution_id,
    }


def run_code_review_pipeline(
    *,
    stargate_url: str,
    files: list[str],
    timeout: float,
    pipeline_options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Estimate and execute code-review batches in parallel."""
    import sys

    estimate = fetch_pipeline_batches(stargate_url, files, timeout=min(timeout, 30.0))
    warnings = estimate.get("warnings", []) if isinstance(estimate, dict) else []
    for warning in warnings:
        print(
            f"Estimator warning [{warning.get('code', 'unknown')}]: "
            f"{warning.get('name', '?')} - {warning.get('message', '')}",
            file=sys.stderr,
        )

    batches = estimate.get("batches", []) if isinstance(estimate, dict) else []
    if not batches:
        return [
            {
                "batch": {
                    "items": files,
                    "tokens": estimate.get("total_source_tokens", 0),
                },
                "result": {
                    "status": "no_batches",
                    "message": "Estimator returned no batches",
                },
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }
        ]

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(batches)) as pool:
        futures = [
            pool.submit(
                query_code_review_batch,
                stargate_url=stargate_url,
                batch=batch,
                timeout=timeout,
                pipeline_options=pipeline_options,
            )
            for batch in batches
        ]
        for future in as_completed(futures):
            results.append(future.result())
    return results


def build_reviewer_pipeline_model_overrides(models: list[str]) -> dict[str, str]:
    """Map reviewer target models to code-review pipeline model refs."""
    overrides: dict[str, str] = {}
    if not models:
        return overrides
    overrides["review_model"] = models[0]
    if len(models) > 1:
        overrides["validate_model"] = models[1]
    return overrides
