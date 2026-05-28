"""
Pre-loop setup for the assess_loop_v1 handler.

Resolves ``handler_inputs`` to concrete values, seeds the mutable artifact,
compresses list-of-dict inputs into numbered text, assembles the shared
``base_ctx`` mapping, and renders the cached system prompt once (stable
KV-cache prefix reused by every assess/action call).

The result is returned as a frozen :class:`LoopSetup` container so the loop
runner receives immutable setup inputs distinct from the mutable per-iteration
:class:`LoopState`. Behaviour mirrors the monolith's setup block exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...execution.resolver import NamespaceResolver
from .artifact_text import _format_text_list

if TYPE_CHECKING:
    from ...schemas import StepConfig
    from ..assess_loop_config import AssessLoopConfig
    from ..builtin import BaseHandler
    from ..protocol import PipelineContext


@dataclass(slots=True, frozen=True)
class LoopSetup:
    """Immutable inputs assembled once before the assess loop runs.

    Distinct from :class:`LoopState`: this carries the resolved, non-mutating
    setup surface (resolved inputs, seed artifact, shared prompt context, cached
    system prompt), whereas ``LoopState`` carries the per-iteration mutable
    bookkeeping. ``artifact`` / ``artifact_raw`` are the *initial* values; the
    loop runner rebinds its own locals from these and never mutates the dataclass.
    """

    resolved: dict[str, Any]
    artifact: str
    artifact_raw: str
    base_ctx: dict[str, Any]
    cached_sys: str | None


def resolve_loop_setup(
    handler: BaseHandler,
    step: StepConfig,
    context: PipelineContext,
    cfg: AssessLoopConfig,
) -> LoopSetup:
    """Resolve handler inputs and assemble the shared loop context.

    Steps (identical order/semantics to the former ``execute`` setup block):

    1. Resolve every ``handler_inputs`` binding via :class:`NamespaceResolver`.
    2. Seed ``cfg.artifact_key`` from ``cfg.initial_artifact`` when absent, else
       raise ``ValueError`` (no artifact source).
    3. Compress list-of-dict ``"text"`` inputs into numbered plain text, leaving
       the artifact (always a plain string) untouched.
    4. Build ``base_ctx`` = source_text + pipeline options + resolved inputs.
    5. Render ``system_prompt_ref`` once (excluding the artifact key) as the
       cached system prompt, or ``None`` when no system prompt is configured.

    Raises:
        ValueError: artifact_key absent from handler_inputs and no initial_artifact.
    """
    resolver = NamespaceResolver(context)
    resolved: dict[str, Any] = {
        name: handler._resolve_input(resolver, step, name, step.handler_inputs)
        for name in step.handler_inputs
    }

    if cfg.artifact_key not in resolved:
        if cfg.initial_artifact is not None:
            # Seed the artifact from the literal initial_artifact domain field
            # (e.g. "" to bootstrap a coverage-check loop without a prior step).
            # The key is injected into resolved so downstream contexts stay
            # consistent.
            resolved[cfg.artifact_key] = cfg.initial_artifact
        else:
            raise ValueError(
                f"Step '{step.id}': artifact_key '{cfg.artifact_key}' not in "
                f"handler_inputs and no initial_artifact provided. "
                f"Available: {list(resolved.keys())}"
            )

    # Compress list-of-dict inputs that carry a "text" field into numbered
    # plain-text lists before they hit the prompt template. Raw JSON reprs
    # of structured fact objects balloon token usage with fields (statement_id,
    # source_sentences, claim_type, …) that are irrelevant to assess/act calls.
    # The artifact is excluded — it is always a plain string from a prior step.
    resolved = {
        k: _format_text_list(v) if k != cfg.artifact_key else v
        for k, v in resolved.items()
    }

    artifact: str = str(resolved[cfg.artifact_key])
    artifact_raw: str = artifact  # unstripped; used by programmatic assess handlers

    # Pipeline options (e.g. max_sloc_per_module) injected so action prompts
    # can reference them as {key} — consistent with generate handler behaviour.
    base_ctx: dict[str, Any] = {
        "text": context.source_text,
        **context.options,
        **resolved,
    }

    # Render system_prompt_ref ONCE (outside loop) — stable KV-cache prefix
    # reused as the system prompt for every assess and action call.
    cached_sys: str | None = None
    if cfg.system_prompt_ref:
        static = {k: v for k, v in base_ctx.items() if k != cfg.artifact_key}
        cached_sys = handler._render_prompt(
            cfg.system_prompt_ref, static, context
        ).user_prompt

    return LoopSetup(
        resolved=resolved,
        artifact=artifact,
        artifact_raw=artifact_raw,
        base_ctx=base_ctx,
        cached_sys=cached_sys,
    )
