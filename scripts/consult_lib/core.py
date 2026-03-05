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

from .constants import DEFAULT_MODELS, DEFAULT_STARGATE_URL
from .context import read_context_files
from .model_selection import (
    fetch_available_model_ids,
    load_roles,
    select_models_for_role,
    split_role_config,
    warn_local_model_for_role,
)
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
    error: str | None = None


def _dict_to_result(d: dict[str, Any]) -> ConsultResult:
    """Convert a raw query result dict into a ConsultResult."""
    return ConsultResult(
        model_id=d.get("model_id", "unknown"),
        response_text=d.get("response", ""),
        prompt_tokens=d.get("prompt_tokens", 0),
        completion_tokens=d.get("completion_tokens", 0),
        latency_ms=d.get("latency_ms", 0.0),
        phase=d.get("phase"),
        error=d.get("error"),
    )


def _resolve_models(
    *,
    models: list[str] | None,
    role: str,
    role_select: dict[str, dict[str, Any]],
    stargate_url: str,
    cloud_only: bool,
) -> list[str]:
    """Resolve target models from explicit list, role selection, or defaults, with cloud-only filtering."""
    if models:
        warn_local_model_for_role(role, models, role_select)
        target = list(models)
    else:
        selected = select_models_for_role(role, role_select, stargate_url)
        if selected:
            print(f"Models for '{role}': {', '.join(selected)}", file=sys.stderr)
            target = selected
        else:
            print(
                f"Selection path: static-defaults ({', '.join(DEFAULT_MODELS)})",
                file=sys.stderr,
            )
            target = list(DEFAULT_MODELS)

    if not cloud_only:
        return target

    available = fetch_available_model_ids(stargate_url)
    if available is None:
        raise RuntimeError(
            "--cloud-only: failed to fetch available models from Stargate"
        )
    cloud = [m for m in target if "/" in m and m in available]
    if not cloud:
        raise RuntimeError(
            "--cloud-only: no matching cloud models available. "
            f"Selected: {target}, available cloud: {sorted(m for m in available if '/' in m)}"
        )
    excluded = [m for m in target if "/" not in m]
    if excluded:
        print(
            f"--cloud-only: excluded local models: {', '.join(excluded)}",
            file=sys.stderr,
        )
    return cloud


def _fetch_rag(
    *,
    question: str,
    scope: str,
    rag_url: str,
    stargate_url: str,
    use_pipeline: bool,
) -> list[str] | None:
    """Fetch RAG context via pipeline or direct search, returning findings or None."""
    if not rag_socket_present(rag_url):
        print(
            "RAG service not available (socket absent); running without context retrieval",
            file=sys.stderr,
        )
        return None

    available_scopes = fetch_scope_choices(rag_url=rag_url)
    if scope not in available_scopes:
        print(
            f"Unknown scope '{scope}'. Available: {', '.join(sorted(available_scopes))}. "
            "Skipping RAG.",
            file=sys.stderr,
        )
        return None

    print("Retrieving RAG context...", file=sys.stderr)
    if use_pipeline:
        findings, error = fetch_rag_pipeline(
            question,
            stargate_url=stargate_url,
            rag_url=rag_url,
            scope_override=scope,
        )
    else:
        findings, error = fetch_rag_direct(question, rag_url=rag_url, scope=scope)

    if error:
        print(f"RAG: {error} (continuing without)", file=sys.stderr)
        return None
    if findings:
        print(f"RAG: {len(findings)} findings", file=sys.stderr)
    return findings or None


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
    timeout: float = 300.0,
    chain_directive: str | None = None,
    cloud_only: bool = False,
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
        timeout: Per-model timeout in seconds.
        chain_directive: Custom reviewer directive for chained mode.
        cloud_only: Restrict to cloud models only.

    Returns:
        List of ConsultResult, one per model queried.
    """
    raw_roles = load_roles()
    role_prompts, role_select = split_role_config(raw_roles)

    if role not in role_prompts:
        raise ValueError(f"Unknown role '{role}'. Available: {', '.join(role_prompts)}")

    system_prompt = role_prompts[role]

    target_models = _resolve_models(
        models=models,
        role=role,
        role_select=role_select,
        stargate_url=stargate_url,
        cloud_only=cloud_only,
    )

    if chain and len(target_models) < 2:
        raise ValueError(
            f"Chained consultation requires >= 2 models, got {len(target_models)}"
        )

    file_context: list[str] | None = None
    if context_files:
        file_context = read_context_files([str(p) for p in context_files])
        if file_context:
            print(f"Read {len(file_context)} context blocks", file=sys.stderr)

    rag_findings: list[str] | None = None
    effective_scope = scope or "project"
    if not no_rag:
        rag_findings = _fetch_rag(
            question=question,
            scope=effective_scope,
            rag_url=rag_url,
            stargate_url=stargate_url,
            use_pipeline=use_rag_pipeline,
        )

    context_parts: list[str] = []
    if file_context:
        context_parts.extend(file_context)
    if context_text:
        context_parts.append(context_text)
    user_prompt = build_prompt(
        question,
        rag_findings,
        context_parts,
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

    return [_dict_to_result(r) for r in raw_results]
