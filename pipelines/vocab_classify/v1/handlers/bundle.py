"""Merge per-scope rows for vocab_classify pipeline (fan-in).

Zips load_hints scope rows with retrieve_samples outputs into a consolidated
array of scope bundles for per-scope classification. Applies deterministic
noise filtering to terms before they reach the LLM.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, ClassVar

from systems.pipeline.core.execution.map_reduce import MapOutputCollection
from systems.pipeline.core.handlers.protocol import PipelineContext, StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

# ── Deterministic noise filters ──────────────────────────────────────────────
# These patterns match terms that are never useful vocabulary, regardless of
# scope or domain. Filtering here avoids wasting LLM tokens on obvious noise.

# Single letters, bare symbols, Greek letters
_SINGLE_CHAR_RE = re.compile(r"^[a-zA-Zα-ωΑ-Ω0-9θφψ∅∑∏∂∇]{1,2}$")

# Document structure: "theorem 4.1", "lemma a.1", "figure 4", "table 2",
# "section 3.2", "appendix b", "corollary 2.3", "definition 1"
_DOC_STRUCTURE_RE = re.compile(
    r"^(?:theorem|lemma|corollary|definition|proposition|proof|remark|"
    r"figure|fig\.|table|tbl\.|section|appendix|chapter|example)\s+"
    r"[a-zA-Z0-9.]+$",
    re.IGNORECASE,
)

# Author citation fragments: "et al.", "smith et al. (2021)", "guijarro-ordonez et al. (2021)"
_CITATION_RE = re.compile(
    r"(?:et\s+al\.?)|"
    r"(?:^[a-z][\w-]*(?:\s+(?:et\s+al\.?|\(\d{4}\)))+$)",
    re.IGNORECASE,
)

# Math variables: "z[q]", "θ[q]", "x_i", "w*", bare subscripted/bracketed symbols
_MATH_VAR_RE = re.compile(r"^[a-zA-Zα-ωΑ-Ωθφψ][_\[\(][a-zA-Z0-9,*]+[\]\)]?$")

# Overly generic words with no domain signal
_GENERIC_TERMS: frozenset[str] = frozenset(
    {
        "model",
        "models",
        "system",
        "systems",
        "data",
        "method",
        "methods",
        "results",
        "approach",
        "approaches",
        "performance",
        "analysis",
        "evaluation",
        "framework",
        "implementation",
        "algorithm",
        "algorithms",
        "process",
        "technique",
        "techniques",
        "solution",
        "solutions",
        "strategy",
        "problem",
        "application",
        "applications",
    }
)


def _is_noise(term: str) -> bool:
    """Return True if a term matches any deterministic noise pattern."""
    t = term.strip()
    if not t:
        return True
    if _SINGLE_CHAR_RE.match(t):
        return True
    if _DOC_STRUCTURE_RE.match(t):
        return True
    if _CITATION_RE.search(t):
        return True
    if _MATH_VAR_RE.match(t):
        return True
    if t.lower() in _GENERIC_TERMS:
        return True
    return False


def filter_noise_terms(terms: list[str]) -> list[str]:
    """Remove deterministic noise from a term list."""
    clean = [t for t in terms if not _is_noise(t)]
    dropped = len(terms) - len(clean)
    if dropped:
        logger.debug("Filtered %d noise term(s) from %d", dropped, len(terms))
    return clean


def _truncate_sample_retrieval(
    sample: dict[str, Any],
    *,
    max_chunks: int,
    max_chunk_chars: int,
) -> dict[str, Any]:
    """Keep only lean chunk text so local-slot prompts stay under budget."""
    chunks_raw = sample.get("chunks")
    if not isinstance(chunks_raw, list):
        return {"chunks": []}
    lean: list[dict[str, str]] = []
    for item in chunks_raw[:max_chunks]:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = str(
                item.get("text")
                or item.get("content")
                or item.get("chunk")
                or item.get("body")
                or ""
            )
        else:
            continue
        text = " ".join(text.split())
        if not text:
            continue
        if len(text) > max_chunk_chars:
            text = text[: max_chunk_chars - 1].rstrip() + "…"
        lean.append({"text": text})
    return {"chunks": lean}


# ── Handler ──────────────────────────────────────────────────────────────────


class VocabClassifyBundleV1Handler:
    """Zip parallel map outputs into per-scope bundles for classification."""

    step_type = "vocab_classify_bundle_v1"
    dependency_fields: ClassVar[tuple[str, ...]] = ("bundle_upstream_steps",)

    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        hints_scopes = context.get_output("load_hints")
        if hints_scopes is None or not isinstance(hints_scopes.json, dict):
            raise ValueError("bundle: missing load_hints.json")
        rows: list[dict[str, Any]] = list(hints_scopes.json.get("scopes") or [])
        if not rows:
            payload: dict[str, Any] = {"scopes": []}
            raw = json.dumps(payload)
            return StepOutput(raw=raw, json=payload)

        samples = context.get_output("retrieve_samples")
        if not isinstance(samples, MapOutputCollection):
            raise TypeError("bundle: retrieve_samples must be a map collection")

        s_out = samples.all_outputs()
        n = len(rows)
        if len(s_out) != n:
            raise ValueError(f"bundle: length mismatch hints={n} samples={len(s_out)}")

        max_terms = int(step.get_domain_field("max_terms", 40))
        max_chunk_chars = int(step.get_domain_field("max_chunk_chars", 600))
        max_chunks = int(step.get_domain_field("max_chunks", 2))

        merged: list[dict[str, Any]] = []
        for i in range(n):
            base = dict(rows[i])
            raw_terms = base.get("terms") or []
            if isinstance(raw_terms, list):
                cleaned = filter_noise_terms(
                    [str(t).strip() for t in raw_terms if str(t).strip()]
                )
                base["terms"] = cleaned[:max_terms]
            sample = s_out[i].json if isinstance(s_out[i].json, dict) else {}
            base["sample_retrieval"] = _truncate_sample_retrieval(
                sample,
                max_chunks=max_chunks,
                max_chunk_chars=max_chunk_chars,
            )
            merged.append(base)

        payload = {"scopes": merged}
        raw = json.dumps(payload, ensure_ascii=False)
        return StepOutput(raw=raw, json=payload)
