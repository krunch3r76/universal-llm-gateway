"""Retrieval quality evaluation service.

Sends the pipeline's final context (from ``rerank_assemble`` or
``retrieve_assemble``) to cloud models for structured critique.  The model
scores relevance, coverage, noise, and rewrite quality, then returns a JSON
assessment that can be saved and compared across runs.

Usage (via CLI)::

    python -m tools.pipeline_test eval-retrieval fixtures/rag-context_abc.json
    python -m tools.pipeline_test eval-retrieval --latest rag-context \\
        --models openai/gpt-4.1 google/gemini-2.5-pro --output /tmp/eval.json

Output JSON schema::

    {
      "query": str,
      "fixture": str,
      "execution_id": str,
      "rewrite": {
        "scope": str,
        "scope_confidence": float,
        "out_of_scope_reason": str,
        "rewritten_queries": [str],
        "hyde_passage": str
      },
      "retrieval": {
        "scope_used": str,
        "chunks_found": int,
        "queries_executed": int
      },
      "reranking": {                   // present when rerank_assemble step exists
        "rerank_enabled": bool,
        "chunks_reranked": int,
        "windows_evaluated": int,      // 0 when disabled
        "max_rank_movement": int,
        "rerank_seconds": float
      },
      "evaluations": [
        {
          "model_id": str,
          "scores": {
            "relevance": int,          // 1-10: top chunks directly answer query
            "coverage": int,           // 1-10: important facets covered
            "noise": int,              // 1-10: 10 = no noise (inverted: high = good)
            "rewrite_quality": int     // 1-10: rewritten queries well-formed for corpus
          },
          "verdict": str,             // "good" | "acceptable" | "poor"
          "strengths": [str],
          "issues": [str],
          "recommendations": [str],
          "latency_ms": float,
          "error": str | null
        }
      ]
    }
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from .models import ConsultResult, ExecutionSnapshot

logger = logging.getLogger(__name__)

# Default evaluator models when --models is not provided.
DEFAULT_EVAL_RETRIEVAL_MODELS: list[str] = [
    "openai/gpt-5.2",
    "perplexity/sonar-reasoning-pro",
]

# ── prompt constants ─────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a retrieval quality evaluator for a RAG (Retrieval-Augmented Generation) system.
You will be given:
1. The user's original query
2. The query rewrite metadata (rewritten queries, HyDE passage, scope, confidence)
3. The retrieved context chunks (verbatim, as they will be fed to a generation model)

Your task is to evaluate the quality of the retrieval step.

Respond with a single JSON object matching this schema exactly:
{
  "scores": {
    "relevance": <int 1-10>,
    "coverage": <int 1-10>,
    "noise": <int 1-10>,
    "rewrite_quality": <int 1-10>
  },
  "verdict": <"good" | "acceptable" | "poor">,
  "strengths": [<str>, ...],
  "issues": [<str>, ...],
  "recommendations": [<str>, ...]
}

Score definitions:
- relevance (1-10): Are the top chunks directly and specifically relevant to the query?
  10 = every chunk is on-point; 1 = nothing retrieved is relevant.
- coverage (1-10): Do the chunks collectively cover the key facets the query asks about?
  10 = complete coverage; 1 = major gaps.
- noise (1-10, higher is better): How free is the retrieved set from off-topic or
  tangentially-related content?  10 = zero noise; 1 = mostly irrelevant chunks.
- rewrite_quality (1-10): Are the rewritten queries and HyDE passage well-formed,
  specific, and likely to retrieve relevant content from an academic corpus?
  10 = excellent vocabulary alignment; 1 = vague or misleading rewrites.

verdict:
- "good"       if all scores >= 7
- "poor"       if any score <= 4
- "acceptable" otherwise

strengths: 1-3 bullet points on what the retrieval did well.
issues: 1-3 specific problems identified (empty list if none).
recommendations: 1-3 concrete changes to rewriting strategy or retrieval config
  that would improve quality (empty list if none).

Respond with valid JSON only — no prose, no markdown fences.\
"""

_CONTEXT_CHAR_LIMIT = 12_000
"""Max chars of retrieved context sent to the evaluator to stay within context budgets."""

_RAW_RESPONSE_PREVIEW_LEN = 800
"""When parse fails, store this many chars of raw response in eval output for diagnosis."""


def _build_eval_prompt(
    query: str,
    rewrite: dict[str, Any],
    retrieved_context: str,
    chunks_found: int,
    scope_used: str,
    reranking: dict[str, Any] | None = None,
) -> str:
    """Build the evaluation prompt sent to the retrieval quality evaluator.

    Includes the original query, rewrite metadata, retrieval summary, and
    retrieved context (truncated to _CONTEXT_CHAR_LIMIT when needed).

    Args:
        query: The user's original query.
        rewrite: Rewrite metadata (scope, confidence, rewritten_queries, hyde_passage).
        retrieved_context: Verbatim retrieved context chunks.
        chunks_found: Number of chunks found during retrieval.
        scope_used: Scope used for retrieval.
        reranking: Optional reranking step metadata when enabled
            (rerank_enabled, windows_evaluated, max_rank_movement, etc.).

    Returns:
        Formatted evaluation prompt string.
    """
    context_snippet = retrieved_context
    truncated = False
    if len(context_snippet) > _CONTEXT_CHAR_LIMIT:
        context_snippet = context_snippet[:_CONTEXT_CHAR_LIMIT]
        truncated = True

    rewrite_summary_data = {
        "scope": rewrite.get("scope", "unknown"),
        "scope_confidence": rewrite.get("scope_confidence"),
        "out_of_scope_reason": rewrite.get("out_of_scope_reason", ""),
        "rewritten_queries": rewrite.get("rewritten_queries", []),
        "hyde_passage": rewrite.get("hyde_passage", ""),
    }
    rewrite_summary = json.dumps(
        rewrite_summary_data, indent=2, ensure_ascii=False
    )

    trunc_note = (
        f"\n[Context truncated at {_CONTEXT_CHAR_LIMIT} chars for evaluation]"
        if truncated
        else ""
    )

    retrieval_lines = [
        f"- Scope used for retrieval: {scope_used}",
        f"- Chunks found: {chunks_found}",
    ]
    if reranking is not None:
        enabled = reranking.get("rerank_enabled", False)
        retrieval_lines.append(
            f"- LLM reranking: {'enabled' if enabled else 'disabled (passthrough)'}"
        )
        if enabled:
            retrieval_lines.append(
                f"- Windows evaluated: {reranking.get('windows_evaluated', 0)}"
            )
            retrieval_lines.append(
                f"- Max rank movement: {reranking.get('max_rank_movement', 0)}"
            )

    retrieval_section = "\n".join(retrieval_lines)

    return (
        f"## Original Query\n{query}\n\n"
        f"## Query Rewrite Metadata\n```json\n{rewrite_summary}\n```\n\n"
        f"## Retrieval Summary\n{retrieval_section}\n\n"
        f"## Retrieved Context\n{context_snippet}{trunc_note}"
    )


def evaluate_retrieval(
    snapshot: ExecutionSnapshot,
    *,
    models: list[str] | None = None,
    stargate_url: str = "http://localhost:9999",
    timeout: float = 120.0,
    parallel: bool = False,
) -> dict[str, Any]:
    """Run retrieval quality evaluation against one or more cloud models.

    Reads ``analyze_scope`` (and optionally ``generate_rewrites``) and the
    final context step (``rerank_assemble`` if present, otherwise
    ``retrieve_assemble``) from the snapshot, assembles the evaluation prompt,
    and queries models via ``execute_consult``.

    Returns a structured result dict matching the module-level schema.
    """
    sys.path.insert(0, (Path(__file__).resolve().parents[2] / "scripts").as_posix())
    from consult_lib.core import execute_consult

    scope_step = snapshot.steps.get("analyze_scope")
    if scope_step is None:
        raise ValueError("Fixture has no 'analyze_scope' step")

    rewrite_step = snapshot.steps.get("generate_rewrites")
    scope_out: dict[str, Any] = scope_step.json_output or {}
    rewrite_result: dict[str, Any] = (
        rewrite_step.json_output if rewrite_step else {}
    ) or {}
    rewrite_out = {
        "scope": scope_out.get("scope", "unknown"),
        "scope_confidence": scope_out.get("scope_confidence"),
        "out_of_scope_reason": scope_out.get("out_of_scope_reason", ""),
        "needs_retrieval": scope_out.get("needs_retrieval", True),
        "rewritten_queries": rewrite_result.get("rewritten_queries", []),
        "hyde_passage": rewrite_result.get("hyde_passage", ""),
    }

    rerank_step = snapshot.steps.get("rerank_assemble")
    retrieve_step = snapshot.steps.get("retrieve_assemble")

    context_step = rerank_step or retrieve_step
    if context_step is None:
        raise ValueError(
            "Fixture has neither 'rerank_assemble' nor 'retrieve_assemble' step"
        )

    retrieve_out: dict[str, Any] = (
        retrieve_step.json_output if retrieve_step else {}
    ) or {}
    retrieved_context: str = context_step.raw_output or ""

    scope_used: str = str(retrieve_out.get("scope", "unknown"))
    chunks_found: int = int(retrieve_out.get("chunks_found", 0))
    queries_executed: int = int(retrieve_out.get("queries_executed", 0))

    rerank_out: dict[str, Any] | None = None
    if rerank_step is not None:
        rerank_out = rerank_step.json_output or {}

    user_prompt = _build_eval_prompt(
        query=snapshot.source_text,
        rewrite=rewrite_out,
        retrieved_context=retrieved_context,
        chunks_found=chunks_found,
        scope_used=scope_used,
        reranking=rerank_out,
    )

    context_source = "rerank_assemble" if rerank_step else "retrieve_assemble"
    effective_models = models if models is not None else DEFAULT_EVAL_RETRIEVAL_MODELS
    logger.info(
        "eval_retrieval: query=%r scope=%s chunks=%d source=%s evaluating with %s",
        snapshot.source_text[:80],
        scope_used,
        chunks_found,
        context_source,
        effective_models,
    )

    context_with_instructions = f"{_SYSTEM_PROMPT}\n\n---\n\n{user_prompt}"
    lib_results: list[ConsultResult] = list(
        execute_consult(
            question=snapshot.source_text,
            role="researcher",
            context_text=context_with_instructions,
            scope=None,
            no_rag=True,
            chain=not parallel,
            models=effective_models,
            stargate_url=stargate_url,
            timeout=timeout,
        )
    )

    evaluations: list[dict[str, Any]] = []
    for result in lib_results:
        eval_entry: dict[str, Any] = {
            "model_id": result.model_id,
            "latency_ms": result.latency_ms,
            "error": result.error,
        }
        if result.error:
            evaluations.append(eval_entry)
            continue

        parsed = _parse_evaluation(result.response_text, result.model_id)
        eval_entry.update(parsed)
        evaluations.append(eval_entry)

    output: dict[str, Any] = {
        "query": snapshot.source_text,
        "fixture": snapshot.source_dir,
        "execution_id": snapshot.execution_id,
        "rewrite": rewrite_out,
        "retrieval": {
            "scope_used": scope_used,
            "chunks_found": chunks_found,
            "queries_executed": queries_executed,
        },
        "evaluations": evaluations,
    }

    if rerank_out is not None:
        output["reranking"] = {
            "rerank_enabled": rerank_out.get("rerank_enabled", False),
            "chunks_reranked": rerank_out.get("chunks_reranked", 0),
            "windows_evaluated": rerank_out.get("windows_evaluated", 0),
            "max_rank_movement": rerank_out.get("max_rank_movement", 0),
            "rerank_seconds": rerank_out.get("rerank_seconds", 0.0),
        }

    return output


def _parse_evaluation(text: str, model_id: str) -> dict[str, Any]:
    """Parse a model's JSON evaluation response; return safe defaults on error."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(
            ln for ln in lines if not ln.strip().startswith("```")
        ).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("eval_retrieval: %s returned invalid JSON: %s", model_id, exc)
        preview = (text or "")[:_RAW_RESPONSE_PREVIEW_LEN]
        issues_list = [f"Could not parse model response as JSON: {exc}"]
        if not cleaned:
            issues_list.append(
                "Response was empty (possible refusal, content filter, or max_tokens=0)."
            )
        elif not cleaned.startswith("{"):
            issues_list.append(
                "Response did not start with JSON (possible prose/markdown or truncation)."
            )
        if preview:
            issues_list.append(
                f"Raw response preview (first {min(len(preview), _RAW_RESPONSE_PREVIEW_LEN)} chars): "
                f"{preview!r}"
            )
        result: dict[str, Any] = {
            "scores": {},
            "verdict": "parse_error",
            "strengths": [],
            "issues": issues_list,
            "recommendations": [],
        }
        if preview:
            result["raw_response_preview"] = preview
        return result

    scores: dict[str, int] = data.get("scores", {})
    _coerce_scores(scores)

    # Derive verdict from scores if model omitted/miscased it
    verdict = str(data.get("verdict", "")).lower()
    if verdict not in {"good", "acceptable", "poor"}:
        verdict = _derive_verdict(scores)

    return {
        "scores": scores,
        "verdict": verdict,
        "strengths": data.get("strengths", []),
        "issues": data.get("issues", []),
        "recommendations": data.get("recommendations", []),
    }


def _coerce_scores(scores: dict[str, Any]) -> None:
    """Coerce score values to int in-place; remove keys if coercion fails."""
    for key in ("relevance", "coverage", "noise", "rewrite_quality"):
        if key in scores:
            try:
                scores[key] = int(scores[key])
            except (TypeError, ValueError):
                scores.pop(key, None)


def _derive_verdict(scores: dict[str, int]) -> str:
    """Derive verdict from scores when the model omits or miscases it.

    _coerce_scores guarantees remaining values are int. Returns 'unknown'
    if no scores are present.
    """
    vals = list(scores.values())
    if not vals:
        return "unknown"
    if all(v >= 7 for v in vals):
        return "good"
    if any(v <= 4 for v in vals):
        return "poor"
    return "acceptable"


def format_eval_result(result: dict[str, Any]) -> str:
    """Format evaluation result for terminal output."""
    lines: list[str] = [
        f"\nQuery: {result['query']}",
        f"Execution: {result['execution_id']}",
        f"Scope used: {result['retrieval']['scope_used']} | "
        f"Chunks: {result['retrieval']['chunks_found']} | "
        f"Queries: {result['retrieval']['queries_executed']}",
    ]

    reranking = result.get("reranking")
    if reranking is not None:
        enabled = reranking.get("rerank_enabled", False)
        if enabled:
            lines.append(
                f"Reranking: enabled | "
                f"Windows: {reranking.get('windows_evaluated', 0)} | "
                f"Max movement: {reranking.get('max_rank_movement', 0)} | "
                f"Time: {reranking.get('rerank_seconds', 0.0):.2f}s"
            )
        else:
            lines.append("Reranking: disabled (passthrough)")

    oor = result["rewrite"].get("out_of_scope_reason", "")
    if oor:
        lines.append(f"Out-of-scope: {oor}")

    lines.append(f"\nRewrites: {result['rewrite']['rewritten_queries']}")

    for ev in result["evaluations"]:
        lines.append(f"\n{'=' * 72}")
        lines.append(f"EVALUATOR: {ev['model_id']}")
        if ev.get("error"):
            lines.append(f"  ERROR: {ev['error']}")
            continue

        scores = ev.get("scores", {})
        score_str = "  ".join(
            f"{k}={scores.get(k, '?')}/10"
            for k in ("relevance", "coverage", "noise", "rewrite_quality")
        )
        verdict = ev.get("verdict", "?").upper()
        lines.append(f"  Verdict: {verdict}  |  {score_str}")

        if verdict == "PARSE_ERROR" and ev.get("raw_response_preview"):
            preview = ev["raw_response_preview"]
            lines.append(f"  Raw response preview: {preview[:200]!r}")

        if ev.get("strengths"):
            lines.append("  Strengths:")
            for s in ev["strengths"]:
                lines.append(f"    + {s}")
        if ev.get("issues"):
            lines.append("  Issues:")
            for i in ev["issues"]:
                lines.append(f"    - {i}")
        if ev.get("recommendations"):
            lines.append("  Recommendations:")
            for r in ev["recommendations"]:
                lines.append(f"    → {r}")

    lines.append("=" * 72)
    return "\n".join(lines)
