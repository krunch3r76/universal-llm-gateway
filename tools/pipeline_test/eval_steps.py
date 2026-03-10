"""Per-step quality evaluation for the rag-context pipeline.

Sends all three step outputs to cloud models in a single call so the evaluator
can reason about where quality degraded — not just what the final context looks
like. Produces per-step scores plus a ``bottleneck`` field identifying the
weakest link.

Usage (via CLI)::

    python -m tools.pipeline_test eval-steps fixtures/rag-context_abc.json
    python -m tools.pipeline_test eval-steps --latest rag-context \\
        --models openai/gpt-4.1 google/gemini-2.5-pro --output /tmp/steps.json

Output JSON schema::

    {
      "query": str,
      "fixture": str,
      "execution_id": str,
      "pipeline_summary": {
        "scope": str,
        "scope_confidence": float,
        "rewritten_queries": [str],
        "chunks_found": int,
        "reranking_enabled": bool
      },
      "evaluations": [
        {
          "model_id": str,
          "latency_ms": float,
          "error": str | null,
          "bottleneck": str,            // "rewrite" | "retrieval" | "reranking" | "none"
          "bottleneck_reason": str,
          "rewrite": {
            "query_quality": int,       // 1-10: rewrites cover query facets, specific vocab
            "scope_accuracy": int,      // 1-10: correct scope + confidence well-calibrated
            "hyde_quality": int,        // 1-10: hypothetical passage plausible for corpus
            "verdict": str,
            "issues": [str],
            "recommendations": [str]
          },
          "retrieval": {
            "relevance": int,           // 1-10: pre-rerank chunks directly answer query
            "coverage": int,            // 1-10: key facets covered before reranking
            "noise": int,               // 1-10: 10=zero noise (inverted: higher is better)
            "verdict": str,
            "issues": [str],
            "recommendations": [str]
          },
          "reranking": {                // null when reranking disabled
            "ordering_improvement": int,  // 1-10: top chunks better after rerank
            "noise_reduction": int,       // 1-10: noise chunks demoted
            "verdict": str,
            "issues": [str],
            "recommendations": [str]
          } | null
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

_CONTEXT_CHAR_LIMIT = 6_000
"""Per-section char budget sent to the evaluator (pre-rerank + post-rerank each)."""

_SYSTEM_PROMPT = """\
You are a diagnostic evaluator for a multi-step RAG (Retrieval-Augmented Generation) pipeline.

The pipeline has three steps:
1. analyze_rewrite — rewrites the query into sub-queries and generates a HyDE passage
2. retrieve_assemble — runs parallel retrieval and merges via RRF (no LLM ranking)
3. rerank_assemble — optionally reranks retrieved chunks using an LLM sliding window

You will be given all three step outputs. Your job is to score each step independently,
then identify the BOTTLENECK: the step most responsible for any quality shortfall.

Respond with a single JSON object matching this schema exactly — no prose, no markdown fences:
{
  "bottleneck": <"rewrite" | "retrieval" | "reranking" | "none">,
  "bottleneck_reason": <one sentence explaining why this step is the weakest link>,
  "rewrite": {
    "query_quality": <int 1-10>,
    "scope_accuracy": <int 1-10>,
    "hyde_quality": <int 1-10>,
    "verdict": <"good" | "acceptable" | "poor">,
    "issues": [<str>, ...],
    "recommendations": [<str>, ...]
  },
  "retrieval": {
    "relevance": <int 1-10>,
    "coverage": <int 1-10>,
    "noise": <int 1-10>,
    "verdict": <"good" | "acceptable" | "poor">,
    "issues": [<str>, ...],
    "recommendations": [<str>, ...]
  },
  "reranking": <null if disabled, otherwise {
    "ordering_improvement": <int 1-10>,
    "noise_reduction": <int 1-10>,
    "verdict": <"good" | "acceptable" | "poor">,
    "issues": [<str>, ...],
    "recommendations": [<str>, ...]
  }>
}

Score definitions:

REWRITE STEP:
- query_quality (1-10): Do the rewritten sub-queries cover distinct facets of the original
  query with precise, corpus-appropriate vocabulary? 10 = excellent diversity and specificity.
- scope_accuracy (1-10): Is the scope classification correct for the query? Is the confidence
  score well-calibrated (not overconfident on a weak match)? 10 = correct scope, calibrated.
- hyde_quality (1-10): Is the HyDE passage a plausible, information-dense excerpt that would
  embed near relevant corpus documents? 10 = dense, specific, corpus-aligned.

RETRIEVAL STEP (evaluate the PRE-RERANK chunk set):
- relevance (1-10): Do the top retrieved chunks directly address the query?
  10 = every chunk is on-point; 1 = nothing retrieved is relevant.
- coverage (1-10): Do the chunks collectively cover the key facets the query asks about?
  10 = complete coverage of all important angles.
- noise (1-10, higher is better): How free is the pre-rerank set from off-topic content,
  metadata files, bibliography lists, table-of-contents entries, or tangentially-related chunks?
  10 = zero noise; 1 = mostly irrelevant.

RERANKING STEP (only when reranking was enabled — compare PRE vs POST rerank):
- ordering_improvement (1-10): Did reranking move the most relevant chunks higher?
  Compare the pre-rerank top chunks to the post-rerank top chunks.
  10 = significant improvement; 5 = neutral; 1 = reranking degraded ordering.
- noise_reduction (1-10): Did reranking demote noise chunks (bibliographies, manifests,
  tangential content) relative to their pre-rerank positions?
  10 = noise clearly demoted; 5 = no change; 1 = noise promoted.

BOTTLENECK:
Identify which step is most responsible for any quality shortfall in the final output.
- "rewrite": query rewrites were too generic, scope was wrong, or HyDE passage was weak
- "retrieval": corpus gap or embedding mismatch — good rewrites but wrong chunks returned
- "reranking": reranker made ordering worse or failed to demote noise
- "none": all steps performed well, no significant bottleneck

verdicts per step:
- "good"       if all scores for that step >= 7
- "poor"       if any score for that step <= 4
- "acceptable" otherwise

issues: up to 3 specific problems identified (empty list if none).
recommendations: up to 3 concrete, actionable changes (empty list if none).\
"""


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _build_eval_prompt(
    query: str,
    rewrite_out: dict[str, Any],
    retrieve_raw: str,
    retrieve_meta: dict[str, Any],
    rerank_raw: str | None,
    rerank_meta: dict[str, Any] | None,
) -> str:
    """Build the multi-step evaluation prompt."""
    sections: list[str] = [f"## Original Query\n{query}"]

    # Rewrite step
    rewrite_summary = json.dumps(
        {
            "scope": rewrite_out.get("scope", "unknown"),
            "scope_confidence": rewrite_out.get("scope_confidence"),
            "out_of_scope_reason": rewrite_out.get("out_of_scope_reason", ""),
            "rewritten_queries": rewrite_out.get("rewritten_queries", []),
            "hyde_passage": rewrite_out.get("hyde_passage", ""),
        },
        indent=2,
        ensure_ascii=False,
    )
    sections.append(
        f"## Step 1: analyze_rewrite output\n```json\n{rewrite_summary}\n```"
    )

    # Retrieve step — pre-rerank chunks
    chunks_found = retrieve_meta.get("chunks_found", 0)
    scope_used = retrieve_meta.get("scope", retrieve_meta.get("scope_used", "unknown"))
    queries_executed = retrieve_meta.get("queries_executed", 0)
    pre_rerank, truncated_pre = _truncate(retrieve_raw, _CONTEXT_CHAR_LIMIT)
    trunc_note = (
        f"\n[Truncated at {_CONTEXT_CHAR_LIMIT} chars]" if truncated_pre else ""
    )
    sections.append(
        f"## Step 2: retrieve_assemble output\n"
        f"- Scope: {scope_used}\n"
        f"- Chunks found: {chunks_found}\n"
        f"- Queries executed: {queries_executed}\n\n"
        f"### Pre-rerank chunks (verbatim)\n{pre_rerank}{trunc_note}"
    )

    # Rerank step — post-rerank chunks (or disabled)
    if rerank_meta is None or not rerank_meta.get("rerank_enabled", False):
        sections.append(
            "## Step 3: rerank_assemble output\nReranking was DISABLED — "
            "post-rerank context is identical to pre-rerank. "
            "Set reranking field to null in your response."
        )
    else:
        windows = rerank_meta.get("windows_evaluated", 0)
        max_move = rerank_meta.get("max_rank_movement", 0)
        seconds = rerank_meta.get("rerank_seconds", 0.0)
        post_rerank_text = rerank_raw or ""
        post_rerank, truncated_post = _truncate(post_rerank_text, _CONTEXT_CHAR_LIMIT)
        trunc_note_post = (
            f"\n[Truncated at {_CONTEXT_CHAR_LIMIT} chars]" if truncated_post else ""
        )
        sections.append(
            f"## Step 3: rerank_assemble output\n"
            f"- Windows evaluated: {windows}\n"
            f"- Max rank movement observed: {max_move}\n"
            f"- Rerank time: {seconds:.2f}s\n\n"
            f"### Post-rerank chunks (verbatim)\n{post_rerank}{trunc_note_post}"
        )

    return "\n\n".join(sections)


def evaluate_steps(
    snapshot: ExecutionSnapshot,
    *,
    models: list[str] | None = None,
    stargate_url: str = "http://localhost:9999",
    timeout: float = 120.0,
    parallel: bool = False,
) -> dict[str, Any]:
    """Run per-step quality evaluation against one or more cloud models.

    Sends all three step outputs in a single prompt so the evaluator can reason
    about attribution — which step caused any quality shortfall. Returns a
    structured result with per-step scores and a bottleneck field.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from consult_lib.core import execute_consult

    rewrite_step = snapshot.steps.get("analyze_rewrite")
    if rewrite_step is None:
        raise ValueError("Fixture has no 'analyze_rewrite' step")
    retrieve_step = snapshot.steps.get("retrieve_assemble")
    if retrieve_step is None:
        raise ValueError("Fixture has no 'retrieve_assemble' step")
    rerank_step = snapshot.steps.get("rerank_assemble")

    rewrite_out: dict[str, Any] = rewrite_step.json_output or {}
    retrieve_meta: dict[str, Any] = retrieve_step.json_output or {}
    retrieve_raw: str = retrieve_step.raw_output or ""
    rerank_meta: dict[str, Any] | None = (
        rerank_step.json_output if rerank_step else None
    )
    rerank_raw: str | None = rerank_step.raw_output if rerank_step else None

    reranking_enabled = bool(rerank_meta and rerank_meta.get("rerank_enabled", False))

    user_prompt = _build_eval_prompt(
        query=snapshot.source_text,
        rewrite_out=rewrite_out,
        retrieve_raw=retrieve_raw,
        retrieve_meta=retrieve_meta,
        rerank_raw=rerank_raw,
        rerank_meta=rerank_meta,
    )

    context_with_instructions = f"{_SYSTEM_PROMPT}\n\n---\n\n{user_prompt}"

    logger.info(
        "eval_steps: query=%r scope=%s chunks=%d reranking=%s evaluating with %s",
        snapshot.source_text[:80],
        retrieve_meta.get("scope", "unknown"),
        retrieve_meta.get("chunks_found", 0),
        reranking_enabled,
        models or "auto-selected cloud models",
    )

    lib_results: list[ConsultResult] = [
        ConsultResult(
            model_id=r.model_id,
            response_text=r.response_text,
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
            latency_ms=r.latency_ms,
            error=r.error,
        )
        for r in execute_consult(
            question=snapshot.source_text,
            role="researcher",
            context_text=context_with_instructions,
            scope=None,
            no_rag=True,
            chain=not parallel,
            models=models,
            stargate_url=stargate_url,
            timeout=timeout,
        )
    ]

    evaluations: list[dict[str, Any]] = []
    for result in lib_results:
        entry: dict[str, Any] = {
            "model_id": result.model_id,
            "latency_ms": result.latency_ms,
            "error": result.error,
        }
        if result.error:
            evaluations.append(entry)
            continue
        parsed = _parse_step_evaluation(result.response_text, result.model_id)
        entry.update(parsed)
        evaluations.append(entry)

    return {
        "query": snapshot.source_text,
        "fixture": snapshot.source_dir,
        "execution_id": snapshot.execution_id,
        "pipeline_summary": {
            "scope": rewrite_out.get("scope", "unknown"),
            "scope_confidence": rewrite_out.get("scope_confidence"),
            "rewritten_queries": rewrite_out.get("rewritten_queries", []),
            "chunks_found": retrieve_meta.get("chunks_found", 0),
            "reranking_enabled": reranking_enabled,
        },
        "evaluations": evaluations,
    }


def _parse_step_evaluation(text: str, model_id: str) -> dict[str, Any]:
    """Parse the model's JSON response; return safe defaults on parse error."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(
            ln for ln in lines if not ln.strip().startswith("```")
        ).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("eval_steps: %s returned invalid JSON: %s", model_id, exc)
        return {
            "bottleneck": "unknown",
            "bottleneck_reason": f"Could not parse response: {exc}",
            "rewrite": {},
            "retrieval": {},
            "reranking": None,
        }

    def _coerce(block: dict[str, Any], keys: list[str]) -> None:
        for k in keys:
            if k in block:
                try:
                    block[k] = int(block[k])
                except (TypeError, ValueError):
                    del block[k]

    def _derive_verdict(block: dict[str, Any], score_keys: list[str]) -> str:
        vals = [block[k] for k in score_keys if isinstance(block.get(k), int)]
        if not vals:
            return "unknown"
        if all(v >= 7 for v in vals):
            return "good"
        if any(v <= 4 for v in vals):
            return "poor"
        return "acceptable"

    rewrite = data.get("rewrite") or {}
    _coerce(rewrite, ["query_quality", "scope_accuracy", "hyde_quality"])
    if "verdict" not in rewrite or rewrite["verdict"] not in {
        "good",
        "acceptable",
        "poor",
    }:
        rewrite["verdict"] = _derive_verdict(
            rewrite, ["query_quality", "scope_accuracy", "hyde_quality"]
        )

    retrieval = data.get("retrieval") or {}
    _coerce(retrieval, ["relevance", "coverage", "noise"])
    if "verdict" not in retrieval or retrieval["verdict"] not in {
        "good",
        "acceptable",
        "poor",
    }:
        retrieval["verdict"] = _derive_verdict(
            retrieval, ["relevance", "coverage", "noise"]
        )

    reranking: dict[str, Any] | None = data.get("reranking")
    if reranking is not None:
        _coerce(reranking, ["ordering_improvement", "noise_reduction"])
        if "verdict" not in reranking or reranking["verdict"] not in {
            "good",
            "acceptable",
            "poor",
        }:
            reranking["verdict"] = _derive_verdict(
                reranking, ["ordering_improvement", "noise_reduction"]
            )

    bottleneck = str(data.get("bottleneck", "unknown")).lower()
    if bottleneck not in {"rewrite", "retrieval", "reranking", "none"}:
        bottleneck = "unknown"

    return {
        "bottleneck": bottleneck,
        "bottleneck_reason": str(data.get("bottleneck_reason", "")),
        "rewrite": rewrite,
        "retrieval": retrieval,
        "reranking": reranking,
    }


def format_eval_steps_result(result: dict[str, Any]) -> str:
    """Format per-step evaluation result for terminal output."""
    summary = result["pipeline_summary"]
    lines: list[str] = [
        f"\nQuery: {result['query']}",
        f"Execution: {result['execution_id']}",
        f"Scope: {summary['scope']} (confidence: {summary.get('scope_confidence', '?')})"
        f" | Chunks: {summary['chunks_found']}"
        f" | Reranking: {'enabled' if summary['reranking_enabled'] else 'disabled'}",
        f"Rewrites: {summary['rewritten_queries']}",
    ]

    for ev in result["evaluations"]:
        lines.append(f"\n{'=' * 72}")
        lines.append(f"EVALUATOR: {ev['model_id']}")
        if ev.get("error"):
            lines.append(f"  ERROR: {ev['error']}")
            continue

        bottleneck = ev.get("bottleneck", "?")
        lines.append(
            f"  Bottleneck: {bottleneck.upper()}  — {ev.get('bottleneck_reason', '')}"
        )

        for step_key, label, score_keys in [
            ("rewrite", "REWRITE", ["query_quality", "scope_accuracy", "hyde_quality"]),
            ("retrieval", "RETRIEVAL", ["relevance", "coverage", "noise"]),
        ]:
            block = ev.get(step_key) or {}
            score_str = "  ".join(f"{k}={block.get(k, '?')}/10" for k in score_keys)
            verdict = str(block.get("verdict", "?")).upper()
            lines.append(f"\n  [{label}] {verdict}  |  {score_str}")
            for issue in block.get("issues", []):
                lines.append(f"    - {issue}")
            for rec in block.get("recommendations", []):
                lines.append(f"    → {rec}")

        reranking = ev.get("reranking")
        if reranking is None:
            lines.append("\n  [RERANKING] disabled")
        else:
            score_str = (
                f"ordering_improvement={reranking.get('ordering_improvement', '?')}/10"
                f"  noise_reduction={reranking.get('noise_reduction', '?')}/10"
            )
            verdict = str(reranking.get("verdict", "?")).upper()
            lines.append(f"\n  [RERANKING] {verdict}  |  {score_str}")
            for issue in reranking.get("issues", []):
                lines.append(f"    - {issue}")
            for rec in reranking.get("recommendations", []):
                lines.append(f"    → {rec}")

    lines.append("\n" + "=" * 72)
    return "\n".join(lines)
