"""Domain-vocabulary leak detection for abstract_problem records."""

from __future__ import annotations

import re
from typing import Any


def _normalize_term(term: str) -> str:
    return term.strip().casefold()


def _stem_variant(term: str) -> str:
    t = _normalize_term(term)
    if len(t) > 4 and t.endswith("ing"):
        return t[:-3]
    if len(t) > 3 and t.endswith("s"):
        return t[:-1]
    return t


def _abstract_text(abstract: dict[str, Any]) -> str:
    parts: list[str] = [str(abstract.get("structural_goal", ""))]
    for actor in abstract.get("actors") or []:
        if isinstance(actor, dict):
            parts.extend(str(actor.get(k, "")) for k in ("role", "capability"))
    for resource in abstract.get("resources") or []:
        if isinstance(resource, dict):
            parts.append(str(resource.get("name", "")))
    for constraint in abstract.get("constraints") or []:
        if isinstance(constraint, dict):
            parts.append(str(constraint.get("statement", "")))
    for inv in abstract.get("invariants") or []:
        parts.append(str(inv))
    return " ".join(parts)


def check_leak(
    abstract_problem: dict[str, Any],
    domain_terms_to_strip: list[str],
) -> dict[str, Any]:
    """Return leak_report (schema L) for one abstract_problem attempt."""
    text = _abstract_text(abstract_problem)
    folded = text.casefold()
    hits: list[dict[str, str]] = []
    for term in domain_terms_to_strip:
        variants = {_normalize_term(term), _stem_variant(term)}
        for variant in variants:
            if not variant:
                continue
            if variant in folded or re.search(rf"\b{re.escape(variant)}\b", folded):
                hits.append(
                    {
                        "term": term,
                        "variant_matched": variant,
                        "field": "abstract_problem",
                    }
                )
                break
    return {"passed": not hits, "hits": hits, "attempts": 1}


def finalize_leak_reports(
    attempt_reports: list[dict[str, Any] | None],
    attempt_abstracts: list[dict[str, Any] | None],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge per-attempt leak_check outputs into final leak_report + accepted abstract."""
    ran = 0
    accepted: dict[str, Any] | None = None
    merged_hits: list[dict[str, str]] = []
    passed_any = False
    for report, abstract in zip(attempt_reports, attempt_abstracts, strict=False):
        if report is None:
            continue
        if report.get("_skipped"):
            continue
        ran += 1
        if report.get("passed"):
            passed_any = True
            if accepted is None and abstract is not None:
                accepted = abstract
        merged_hits.extend(list(report.get("hits") or []))
    if accepted is None and attempt_abstracts:
        for abstract in reversed(attempt_abstracts):
            if abstract is not None and not abstract.get("_skipped"):
                accepted = abstract
                break
    final = {
        "passed": passed_any,
        "hits": merged_hits if not passed_any else [],
        "attempts": ran,
    }
    return final, accepted or {}
