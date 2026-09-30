"""Pipeline consultation for consult roles that still have a virtual model.

The code-review pipeline is retired. A code review that leaves the tab is
``team_dispatch(op=generate, model=cdp/opus-5.5, job=code-review)``.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from .constants import DEFAULT_STARGATE_URL

CODE_REVIEW_PIPELINE_RETIRED = (
    "The code-review pipeline is retired. "
    "A code review that leaves this tab is "
    "team_dispatch(op=generate, model=cdp/opus-5.5, job=code-review). "
    "cdp/opus-5 is the same class. "
    "In this tab, apply review-task-guidance and do not call model code-review."
)

_PIPELINE_MAP: dict[str, str] = {
    "researcher": "consult-researcher",
    "architect": "consult-architect",
    "planner": "consult-planner",
    "prompt_engineer": "consult-prompt-engineer",
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
    pipeline: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch token-budgeted batch plan from Stargate estimator endpoint."""
    if pipeline == "code-review":
        raise PipelineError(CODE_REVIEW_PIPELINE_RETIRED)
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
    """Refuse. The code-review virtual model is retired."""
    del stargate_url, batch, timeout, pipeline_options
    raise PipelineError(CODE_REVIEW_PIPELINE_RETIRED)


def run_code_review_pipeline(
    *,
    stargate_url: str,
    files: list[str],
    timeout: float,
    pipeline_options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Refuse. Code review is a job=code-review generate, not this pipeline."""
    del stargate_url, files, timeout, pipeline_options
    raise PipelineError(CODE_REVIEW_PIPELINE_RETIRED)


def build_reviewer_pipeline_model_overrides(models: list[str]) -> dict[str, str]:
    """Refuse. Reviewer model overrides targeted the retired code-review pipeline."""
    del models
    raise PipelineError(CODE_REVIEW_PIPELINE_RETIRED)
