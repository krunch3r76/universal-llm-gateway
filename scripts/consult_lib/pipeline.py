"""Code-review pipeline: batch estimation and parallel execution."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx


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
