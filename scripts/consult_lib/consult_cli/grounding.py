"""Grounding validation helpers and attribution remapping for consult execution.

These utilities isolate grounding observation side effects from branch execution
logic to keep pipeline and direct orchestration modules compact and readable.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from typing import Any, Protocol

import httpx

from consult_lib.core import ConsultResult


class GroundingOutcome(Protocol):
    """Structural contract for grounding outcomes consumed by observation logic."""

    model_id: str
    invalid_count: int
    hallucination_ratio: float
    excluded: bool


def _observe_grounding_outcomes(
    grounding_outcomes: list[GroundingOutcome],
    task: str,
    stargate_url: str,
) -> None:
    """POST path-grounding outcomes to Stargate /v1/models/observe (reputation)."""
    url = f"{stargate_url.rstrip('/')}/v1/models/observe"
    observed_entries: list[str] = []
    with httpx.Client(timeout=5.0) as client:
        for outcome in grounding_outcomes:
            model_id: str = outcome.model_id
            invalid_count = int(getattr(outcome, "invalid_count", 0))
            if invalid_count <= 0:
                print(
                    "GROUNDING SIGNAL "
                    + json.dumps(
                        {
                            "signal": "grounding.observation.skipped",
                            "payload": {
                                "task": task,
                                "model_id": model_id,
                                "invalid_count": invalid_count,
                            },
                        },
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
                continue
            ratio = float(getattr(outcome, "hallucination_ratio", 0.0))
            quality_score = max(0.0, 1.0 - ratio)
            outcome_label = (
                "error" if bool(getattr(outcome, "excluded", False)) else "success"
            )
            try:
                response = client.post(
                    url,
                    json={
                        "task": task,
                        "model_id": model_id,
                        "outcome": outcome_label,
                        "latency_ms": 0.0,
                        "quality_score": quality_score,
                    },
                )
                response.raise_for_status()
                observed_entries.append(
                    f"{model_id}({outcome_label},q={quality_score:.2f})"
                )
                if outcome_label == "success":
                    print(
                        "GROUNDING SIGNAL "
                        + json.dumps(
                            {
                                "signal": "grounding.observation.success",
                                "payload": {
                                    "task": task,
                                    "model_id": model_id,
                                    "quality_score": quality_score,
                                },
                            },
                            sort_keys=True,
                        ),
                        file=sys.stderr,
                    )
            except httpx.HTTPStatusError as exc:
                response = exc.response
                print(
                    "GROUNDING SIGNAL "
                    + json.dumps(
                        {
                            "signal": "grounding.observation.http.error",
                            "payload": {
                                "task": task,
                                "model_id": model_id,
                                "error": str(exc),
                                "response_status_code": (
                                    response.status_code
                                    if response is not None
                                    else None
                                ),
                            },
                        },
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
            except httpx.RequestError as exc:
                print(
                    "GROUNDING SIGNAL "
                    + json.dumps(
                        {
                            "signal": "grounding.observation.network.error",
                            "payload": {
                                "task": task,
                                "model_id": model_id,
                                "error": str(exc),
                                "request_url": (
                                    str(exc.request.url)
                                    if exc.request is not None
                                    else None
                                ),
                            },
                        },
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
    if observed_entries:
        print(
            "GROUNDING REPUTATION: observed " + ", ".join(observed_entries),
            file=sys.stderr,
        )


def _remap_grounding_results(
    results: list[ConsultResult],
    selected_models: list[str],
    pipeline_virtual_id: str | None,
) -> list[ConsultResult]:
    """Remap virtual pipeline model IDs to actual model IDs for grounding attribution.

    When a single-model pipeline path runs without explicit --models, ConsultResult.model_id
    is the pipeline virtual ID (e.g. 'consult-architect'). Penalties must be attributed to
    the actual model, not the virtual container.

    Only remaps when: result count == selected_model count AND result.model_id == virtual ID.
    Multi-model pipeline paths already set the correct actual IDs in query_pipeline_multi.

    Args:
        results: Raw consult results returned by a direct or pipeline call.
        selected_models: Concrete model IDs selected for this invocation.
        pipeline_virtual_id: Optional virtual model ID exposed by pipeline wrapper roles.

    Returns:
        Result list with model IDs remapped to concrete providers when possible.
    """
    if not pipeline_virtual_id or len(results) != len(selected_models):
        if not pipeline_virtual_id:
            return results
        print(
            f"WARN: _remap_grounding_results: result count ({len(results)}) != "
            f"selected_model count ({len(selected_models)}); skipping ID remap",
            file=sys.stderr,
        )
        print(
            "GROUNDING SIGNAL "
            + json.dumps(
                {
                    "signal": "grounding.model.id.remap.skipped",
                    "payload": {
                        "pipeline_virtual_id": pipeline_virtual_id,
                        "result_count": len(results),
                        "selected_model_count": len(selected_models),
                    },
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return results
    remapped: list[ConsultResult] = []
    for result, actual in zip(results, selected_models):
        if result.model_id == pipeline_virtual_id and actual != pipeline_virtual_id:
            remapped.append(replace(result, model_id=actual))
            print(
                "GROUNDING SIGNAL "
                + json.dumps(
                    {
                        "signal": "grounding.model.id.remapped",
                        "payload": {
                            "from_model_id": pipeline_virtual_id,
                            "to_model_id": actual,
                        },
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            continue
        remapped.append(result)
    return remapped


def _results_to_dicts(results: list[ConsultResult]) -> list[dict[str, Any]]:
    """Convert ConsultResult list back to dicts for format_output compatibility."""
    out: list[dict[str, Any]] = []
    for result in results:
        data = {k: v for k, v in asdict(result).items() if v is not None}
        response_text = data.pop("response_text", "")
        if result.error:
            data.pop("prompt_tokens", None)
            data.pop("completion_tokens", None)
            data.pop("latency_ms", None)
        else:
            data["response"] = response_text
        out.append(data)
    return out
