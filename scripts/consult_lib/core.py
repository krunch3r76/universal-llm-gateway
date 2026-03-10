"""Core consultation API: execute_consult() is the stable entry point.

All consultation consumers (scripts/consult CLI, pipeline_test consult)
call this function. Implementation changes here propagate to all callers.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from transport_utils.rag_client import DEFAULT_RAG_URL

from .constants import DEFAULT_STARGATE_URL
from .context import read_context_files
from .model_selection import (
    load_roles,
    select_models_for_role,
    split_role_config,
    warn_local_model_for_role,
)
from .pipeline import get_pipeline_id, query_pipeline, query_pipeline_multi
from .query import build_prompt, query_chain, query_parallel
from .rag import (
    fetch_rag_direct,
    fetch_rag_pipeline,
    fetch_scope_choices,
    rag_socket_present,
)


@dataclass(slots=True, kw_only=True)
class ConsultResult:
    """Structured result from a single model consultation."""

    model_id: str
    response_text: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    phase: str | None = None
    phase_index: int | None = None
    error: str | None = None
    selection_path: str | None = None
    execution_id: str | None = None
    pipeline_id: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    duration_ms: float | None = None
    input_prompt_preview: str | None = None
    response_preview: str | None = None


def _dict_to_result(d: dict[str, Any]) -> ConsultResult:
    """Convert a raw query result dict into a ConsultResult.

    Args:
        d: Raw result dict from pipeline or direct query (model_id, response, etc.).

    Returns:
        ConsultResult with fields populated from d.
    """
    return ConsultResult(
        model_id=d.get("model_id", "unknown"),
        response_text=d.get("response", ""),
        prompt_tokens=d.get("prompt_tokens", 0),
        completion_tokens=d.get("completion_tokens", 0),
        latency_ms=d.get("latency_ms", 0.0),
        phase=d.get("phase"),
        phase_index=d.get("phase_index"),
        error=d.get("error"),
        execution_id=d.get("execution_id"),
        pipeline_id=d.get("pipeline_id"),
        started_at=d.get("started_at"),
        finished_at=d.get("finished_at"),
        duration_ms=d.get("duration_ms"),
        input_prompt_preview=d.get("input_prompt_preview"),
        response_preview=d.get("response_preview"),
    )


def _resolve_models(
    *,
    models: list[str] | None,
    role: str,
    role_requirements: dict[str, dict[str, Any]],
    stargate_url: str,
) -> tuple[list[str], str | None]:
    """Resolve target models from explicit list or unified server-side selection.

    Args:
        models: Explicit model IDs, or None to use server-side selection.
        role: Consultation role (researcher, prompt_engineer, etc.).
        role_requirements: Per-role config (source, scope_hint, etc.).
        stargate_url: Stargate base URL for /api/select.

    Returns:
        (model_ids, selection_path); selection_path is None when models were
        explicitly provided.

    Raises SelectionFailure when server-side selection fails (not available,
    HTTP error, or returns no models).
    """
    if models:
        warn_local_model_for_role(role, models, role_requirements)
        return models.copy(), None

    selected, selection_path = select_models_for_role(
        role, role_requirements, stargate_url
    )
    print(f"Models for '{role}': {', '.join(selected)}", file=sys.stderr)
    return selected, selection_path


def _fetch_rag(
    *,
    question: str,
    scope: str,
    rag_url: str,
    stargate_url: str,
    use_pipeline: bool,
    extra_pipeline_options: dict[str, Any] | None = None,
    rag_top_k: int | None = None,
) -> list[str] | None:
    """Fetch RAG context via pipeline or direct search, returning findings or None.

    Args:
        question: User question to retrieve context for.
        scope: RAG scope name (e.g. project, research).
        rag_url: RAG service URL (UDS or TCP).
        stargate_url: Stargate base URL when using pipeline.
        use_pipeline: If True, use rag-context pipeline; if False, direct search.
        extra_pipeline_options: Options merged into pipeline request.
        rag_top_k: For direct search, number of chunks (default 5).

    Returns:
        List of finding strings, or None on failure when use_pipeline=False.

    ∀ error when use_pipeline=True: raise RuntimeError — the rag-context pipeline
    was explicitly requested; silent fallback produces ungrounded output with no
    visible signal to the caller.

    ∀ error when use_pipeline=False (direct search): return None — direct search
    is a best-effort path and its failure modes (socket absent, scope unknown) are
    already surfaced as warnings.
    """
    if not rag_socket_present(rag_url):
        msg = "RAG service not available (socket absent)"
        if use_pipeline:
            raise RuntimeError(msg)
        print(f"{msg}; running without context retrieval", file=sys.stderr)
        return None

    available_scopes = set(fetch_scope_choices(rag_url=rag_url))
    if scope not in available_scopes:
        msg = (
            f"Unknown RAG scope '{scope}'. "
            f"Available: {', '.join(sorted(available_scopes))}."
        )
        if use_pipeline:
            raise RuntimeError(msg)
        print(f"{msg} Skipping RAG.", file=sys.stderr)
        return None

    print("Retrieving RAG context...", file=sys.stderr)
    if use_pipeline:
        findings, error = fetch_rag_pipeline(
            question,
            stargate_url=stargate_url,
            rag_url=rag_url,
            scope_override=scope,
            extra_pipeline_options=extra_pipeline_options,
        )
    else:
        top_k = rag_top_k if rag_top_k is not None else 5
        findings, error = fetch_rag_direct(
            question, rag_url=rag_url, scope=scope, top_k=top_k
        )

    if error:
        if use_pipeline:
            if error.startswith("pipeline.step.error:"):
                print(
                    f"RAG pipeline step failed: {error} (continuing without context)",
                    file=sys.stderr,
                )
                return None
            raise RuntimeError(f"RAG pipeline failed: {error}")
        print(f"RAG: {error} (continuing without)", file=sys.stderr)
        return None
    if findings:
        print(f"RAG: {len(findings)} findings", file=sys.stderr)
    return findings or None


def _derive_consumer_tier(
    *,
    role_requirements: dict[str, dict[str, Any]],
    role: str,
    models: list[str] | None,
    cloud_only: bool,
) -> str | None:
    """Infer consumer tier from role config and explicit model list.

    Args:
        role_requirements: Per-role config (source, scope_hint, etc.).
        role: Consultation role.
        models: Explicit model IDs, or None.
        cloud_only: If True, restrict to cloud models (frontier).

    Resolution:
    1. If explicit models contain "/" (cloud model IDs) → "frontier"
    2. If role requirements declare source: cloud → "frontier"
    3. If cloud_only flag is set → "frontier"
    4. If role requirements declare source: any → "local"
    5. Otherwise → None (let handler use defaults)
    """
    if models and any("/" in m for m in models):
        return "frontier"

    req = role_requirements.get(role, {})
    source = req.get("source", "")

    if cloud_only or source == "cloud":
        return "frontier"
    if source == "any":
        return "local"

    return None


def execute_consult(
    question: str,
    *,
    role: str = "researcher",
    context_files: list[Path] | None = None,
    context_text: str | None = None,
    scope: str | None = None,
    chain: bool = False,
    models: list[str] | None = None,
    stargate_url: str = DEFAULT_STARGATE_URL,
    rag_url: str = DEFAULT_RAG_URL,
    no_rag: bool = False,
    use_rag_pipeline: bool = True,
    rag_top_k: int | None = None,
    timeout: float = 300.0,
    chain_directive: str | None = None,
    cloud_only: bool = False,
    pipeline_options: dict[str, Any] | None = None,
) -> list[ConsultResult]:
    """Execute a consultation against one or more models.

    This is the stable API for all consultation consumers.
    ``scripts/consult`` CLI and ``pipeline_test consult`` both call this.

    Args:
        question: The question to consult on.
        role: Consultation role (researcher, architect, reviewer, planner, prompt_engineer).
        context_files: Files/directories to include as context.
        context_text: Pre-assembled context string (e.g. pipeline step snapshot).
        scope: RAG retrieval scope. None defaults to "project".
        chain: If True, run models sequentially (analyst -> reviewer(s)).
        models: Explicit model IDs. None triggers role-based selection.
        no_rag: Disable RAG retrieval entirely.
        use_rag_pipeline: Use rag-context pipeline (True) or direct search (False).
        rag_top_k: When using direct search, number of chunks to request (default 5).
        timeout: Per-model timeout in seconds.
        chain_directive: Custom reviewer directive for chained mode.
        cloud_only: Restrict to cloud models only.
        pipeline_options: Extra pipeline_options merged into Stargate requests.
            User-supplied keys win on conflict with code-generated keys.

    Returns:
        List of ConsultResult, one per model queried.
    """
    raw_roles = load_roles()
    role_prompts, role_requirements = split_role_config(raw_roles)

    if role not in role_prompts:
        raise ValueError(f"Unknown role '{role}'. Available: {', '.join(role_prompts)}")

    # Gather context (file + RAG) regardless of execution path
    file_context: list[str] | None = None
    if context_files:
        file_context = read_context_files([str(p) for p in context_files])
        if file_context:
            print(f"Read {len(file_context)} context blocks", file=sys.stderr)

    rag_findings: list[str] | None = None
    scope_hint = role_requirements.get(role, {}).get("scope_hint")
    effective_scope = scope or scope_hint or "project"
    if scope_hint and not scope:
        print(
            f"Using role scope hint '{scope_hint}' (override with --scope)",
            file=sys.stderr,
        )

    consumer_tier = _derive_consumer_tier(
        role_requirements=role_requirements,
        role=role,
        models=models,
        cloud_only=cloud_only,
    )

    rag_extra: dict[str, Any] = {}
    if pipeline_options:
        rag_extra.update(pipeline_options)
    if consumer_tier:
        rag_extra["consumer_tier"] = consumer_tier

    if not no_rag:
        rag_findings = _fetch_rag(
            question=question,
            scope=effective_scope,
            rag_url=rag_url,
            stargate_url=stargate_url,
            use_pipeline=use_rag_pipeline,
            extra_pipeline_options=rag_extra or None,
            rag_top_k=rag_top_k,
        )

    context_parts: list[str] = list(file_context or [])
    if context_text:
        context_parts.append(context_text)
    user_prompt = build_prompt(question, rag_findings, context_parts)

    # Pipeline execution path: unified server-side model selection.
    # chain=True still uses the pipeline when one exists — the pipeline handles
    # its own multi-step analysis internally (e.g. consult-planner: analyze → review).
    # Legacy direct-query chain is only used for roles without a pipeline.
    pipeline_id = get_pipeline_id(role)
    if pipeline_id:
        if cloud_only:
            # Pipeline YAMLs enforce source: cloud in model_requirements — the
            # cloud_only flag is redundant on this path but we acknowledge it.
            print(
                "Note: --cloud-only is enforced by pipeline model_requirements",
                file=sys.stderr,
            )
        return _execute_via_pipeline(
            pipeline_id=pipeline_id,
            user_prompt=user_prompt,
            models=models,
            stargate_url=stargate_url,
            timeout=timeout,
            pipeline_options=pipeline_options,
        )

    # Legacy path: direct model queries (roles without a pipeline, or chain mode)
    system_prompt = role_prompts[role]
    effective_requirements = dict(role_requirements)
    if cloud_only and role in effective_requirements:
        effective_requirements[role] = {
            **effective_requirements[role],
            "source": "cloud",
        }

    target_models, selection_path = _resolve_models(
        models=models,
        role=role,
        role_requirements=effective_requirements,
        stargate_url=stargate_url,
    )

    if chain and len(target_models) < 2:
        raise ValueError(
            f"Chained consultation requires >= 2 models, got {len(target_models)}"
        )

    mode = "chained" if chain else "parallel"
    print(
        f"Consulting {len(target_models)} model(s) as {role} ({mode})...",
        file=sys.stderr,
    )

    if chain:
        raw_results = query_chain(
            models=target_models,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            stargate_url=stargate_url,
            timeout=timeout,
            chain_directive=chain_directive,
        )
    else:
        raw_results = query_parallel(
            models=target_models,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            stargate_url=stargate_url,
            timeout=timeout,
        )

    results = [_dict_to_result(r) for r in raw_results]
    if selection_path and results:
        first = results[0]
        results[0] = ConsultResult(
            model_id=first.model_id,
            response_text=first.response_text,
            prompt_tokens=first.prompt_tokens,
            completion_tokens=first.completion_tokens,
            latency_ms=first.latency_ms,
            phase=first.phase,
            phase_index=first.phase_index,
            error=first.error,
            selection_path=selection_path,
            execution_id=first.execution_id,
            pipeline_id=first.pipeline_id,
            started_at=first.started_at,
            finished_at=first.finished_at,
            duration_ms=first.duration_ms,
            input_prompt_preview=first.input_prompt_preview,
            response_preview=first.response_preview,
        )
    return results


def _execute_via_pipeline(
    *,
    pipeline_id: str,
    user_prompt: str,
    models: list[str] | None,
    stargate_url: str,
    timeout: float,
    pipeline_options: dict[str, Any] | None = None,
) -> list[ConsultResult]:
    """Execute consultation through the pipeline virtual model ID.

    Args:
        pipeline_id: Virtual model ID of the pipeline (e.g. consult-planner).
        user_prompt: Assembled user message including context and question.
        models: Explicit model overrides, or None for pipeline default.
        stargate_url: Stargate base URL.
        timeout: Per-request timeout in seconds.
        pipeline_options: Extra options merged into the request.

    Returns:
        List of ConsultResult from the pipeline response(s).
    """
    if models and len(models) > 1:
        print(
            f"Pipeline multi-model: {pipeline_id} × {len(models)} models",
            file=sys.stderr,
        )
        raw_results = query_pipeline_multi(
            pipeline_id=pipeline_id,
            user_message=user_prompt,
            models=models,
            stargate_url=stargate_url,
            timeout=timeout,
            pipeline_options=pipeline_options,
        )
    elif models and len(models) == 1:
        print(
            f"Pipeline: {pipeline_id} (model override: {models[0]})",
            file=sys.stderr,
        )
        raw_results = [
            query_pipeline(
                pipeline_id=pipeline_id,
                user_message=user_prompt,
                stargate_url=stargate_url,
                timeout=timeout,
                model_override=models[0],
                pipeline_options=pipeline_options,
            )
        ]
    else:
        print(f"Pipeline: {pipeline_id} (auto model selection)", file=sys.stderr)
        raw_results = [
            query_pipeline(
                pipeline_id=pipeline_id,
                user_message=user_prompt,
                stargate_url=stargate_url,
                timeout=timeout,
                pipeline_options=pipeline_options,
            )
        ]

    for r in raw_results:
        if isinstance(r, dict) and "pipeline_id" not in r:
            r["pipeline_id"] = pipeline_id
    return [_dict_to_result(r) for r in raw_results]
