"""Output formatting: consultation results and pipeline review output."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


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
) -> str:
    """Format consultation results as structured markdown."""
    lines: list[str] = []
    title = f"# Consultation: {role}" + (" (chained)" if chained else "")
    lines.append(title)
    lines.append(f"**Date**: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Question**: {question}")
    if file_paths:
        lines.append(f"**Context files**: {', '.join(file_paths)}")
    if chained:
        lines.append("**Mode**: analyst -> reviewer(s)")
    if rag_error:
        lines.append(f"**RAG**: failed ({rag_error})")
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
) -> str:
    """Format code-review pipeline batch outputs as markdown."""
    lines: list[str] = []
    lines.append("# Consultation: reviewer (pipeline)")
    lines.append(f"**Date**: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Question**: {question}")
    lines.append(f"**Context files**: {', '.join(file_paths)}")
    lines.append("**Mode**: estimator-driven parallel batches")
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
