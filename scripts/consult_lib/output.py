"""Output formatting: consultation results and pipeline review output."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def _is_cloud_model(model_id: str) -> bool:
    """Classify model IDs using existing cloud-ID convention."""
    return "/" in model_id


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """Return unique items while preserving original order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _format_model_provenance(
    *,
    selected_models: list[str] | None,
    result_models: list[str],
    pipeline_virtual_model: str | None = None,
) -> list[str]:
    """Build markdown lines describing selected and observed model usage.

    When pipeline_virtual_model is set, result_models may include the pipeline
    virtual ID (e.g. "consult-architect") as the model_id field returned by
    Stargate. We distinguish pipeline virtual IDs from actual underlying models
    so agents can tell whether provenance was resolved.

    ∀ pipeline executions where all result_models are virtual IDs:
      we show selected_models as "requested" only; the pipeline response does
      not confirm which underlying model was invoked — verify via OpenRouter or
      pipeline execution events.
    """
    lines: list[str] = []
    selected = _dedupe_preserve_order(selected_models or result_models)
    used = _dedupe_preserve_order(result_models)

    # Separate pipeline virtual IDs from real model IDs in the used list.
    # A pipeline virtual model ID never contains "/" and matches the pipeline_id.
    def _is_pipeline_virtual(mid: str) -> bool:
        return pipeline_virtual_model is not None and mid == pipeline_virtual_model

    real_used = [m for m in used if not _is_pipeline_virtual(m)]
    virtual_used = [m for m in used if _is_pipeline_virtual(m)]

    if pipeline_virtual_model:
        lines.append(f"**Pipeline virtual model**: `{pipeline_virtual_model}`")
        if virtual_used:
            lines.append(
                "**Provenance note**: Response was attributed to the pipeline virtual model ID. "
                "Actual underlying models may appear in partial outputs or pipeline events."
            )

    if selected:
        lines.append(f"**Selected models**: {', '.join(selected)}")

    # When all result IDs are pipeline virtual, we only know what was requested
    # (selected_models), not what the pipeline actually invoked. Stargate returns
    # the pipeline ID as model, not the underlying cloud model.
    effective_used = real_used if real_used else (selected or used)
    used_cloud = [m for m in effective_used if _is_cloud_model(m)]
    used_local = [m for m in effective_used if not _is_cloud_model(m)]
    provenance_resolved = bool(real_used)

    lines.append(
        "**Cloud models requested**: "
        + (", ".join(used_cloud) if used_cloud else "(none)")
        + (" (verify in OpenRouter/pipeline events)" if not provenance_resolved and used_cloud else "")
    )
    lines.append(
        "**Local models requested**: "
        + (", ".join(used_local) if used_local else "(none)")
        + (" (verify in pipeline events)" if not provenance_resolved and used_local else "")
    )
    # Only warn if selected models genuinely did not run (real_used available).
    # Do not warn when provenance is unresolved (real_used is empty).
    if real_used:
        selected_cloud = [m for m in selected if _is_cloud_model(m)]
        selected_local = [m for m in selected if not _is_cloud_model(m)]
        if selected_cloud and not used_cloud:
            lines.append(
                "**Cloud usage note**: Cloud models were selected, but none returned results."
            )
        if selected_local and not used_local:
            lines.append(
                "**Local usage note**: Local models were selected, but none returned results."
            )
    lines.append(
        "**Reminder**: Mention cloud model IDs explicitly when sharing conclusions."
    )
    return lines


def format_result_block(result: dict[str, Any]) -> list[str]:
    """Format a single model result as markdown lines."""
    lines: list[str] = []
    if "error" in result:
        lines.append(f"**Error**: {result['error']}")
    else:
        tokens = result.get("prompt_tokens", 0) + result.get("completion_tokens", 0)
        lines.append(
            f"*{result.get('latency_ms', 0)}ms, "
            f"{tokens} tokens "
            f"({result.get('prompt_tokens', 0)}+{result.get('completion_tokens', 0)})*\n"
        )
        lines.append(result.get("response", ""))
    lines.append("")
    return lines


def format_output(
    question: str,
    role: str,
    results: list[dict[str, Any]],
    rag_error: str | None,
    file_paths: list[str],
    *,
    chained: bool = False,
    selected_models: list[str] | None = None,
    selection_path: str | None = None,
    pipeline_virtual_model: str | None = None,
    call_id: str | None = None,
    run_dir: str | None = None,
) -> str:
    """Format consultation results as structured markdown."""
    lines: list[str] = []
    title = f"# Consultation: {role}" + (" (chained)" if chained else "")
    lines.append(title)
    lines.append(f"**Date**: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Question**: {question}")
    if call_id:
        lines.append(f"**Call ID**: `{call_id}`")
    if run_dir:
        lines.append(f"**Run artifacts**: `{run_dir}`")
    if file_paths:
        lines.append(f"**Context files**: {', '.join(file_paths)}")
    if chained:
        lines.append("**Mode**: analyst -> reviewer(s)")
    if selection_path:
        lines.append(f"**Selection path**: {selection_path}")
    if rag_error:
        lines.append(f"**RAG**: failed ({rag_error})")
    result_models = [str(result.get("model_id", "unknown")) for result in results]
    lines.extend(
        _format_model_provenance(
            selected_models=selected_models,
            result_models=result_models,
            pipeline_virtual_model=pipeline_virtual_model,
        )
    )
    lines.append("")

    for idx, result in enumerate(results):
        model = result.get("model_id", "unknown")
        if chained:
            phase = result.get("phase", "analyst" if idx == 0 else "reviewer")
            lines.append(f"## Phase {idx + 1}: {phase.title()} — {model}")
        else:
            lines.append(f"## {model}")
        lines.extend(format_result_block(result))

    return "\n".join(lines)


def format_pipeline_review_output(
    *,
    question: str,
    file_paths: list[str],
    batches: list[dict[str, Any]],
    selected_models: list[str] | None = None,
    pipeline_virtual_model: str | None = None,
    call_id: str | None = None,
    run_dir: str | None = None,
) -> str:
    """Format code-review pipeline batch outputs as markdown."""
    lines: list[str] = []
    lines.append("# Consultation: reviewer (pipeline)")
    lines.append(f"**Date**: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Question**: {question}")
    if call_id:
        lines.append(f"**Call ID**: `{call_id}`")
    if run_dir:
        lines.append(f"**Run artifacts**: `{run_dir}`")
    lines.append(f"**Context files**: {', '.join(file_paths)}")
    lines.append("**Mode**: estimator-driven parallel batches")
    result_models: list[str] = []
    if selected_models:
        result_models.extend(selected_models)
    lines.extend(
        _format_model_provenance(
            selected_models=selected_models,
            result_models=result_models,
            pipeline_virtual_model=pipeline_virtual_model,
        )
    )
    lines.append("")
    for idx, batch_result in enumerate(batches, 1):
        batch = batch_result.get("batch", {})
        files = batch.get("items", [])
        tokens = batch.get("tokens", 0)
        prompt_tokens = batch_result.get("prompt_tokens", 0)
        completion_tokens = batch_result.get("completion_tokens", 0)
        total_tokens = prompt_tokens + completion_tokens
        lines.append(
            f"## Batch {idx} ({len(files)} files, estimate={tokens} tokens, actual={total_tokens} tokens)"
        )
        lines.append(f"**Files**: {', '.join(files)}")
        lines.append("```json")
        lines.append(json.dumps(batch_result.get("result", {}), indent=2))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)
