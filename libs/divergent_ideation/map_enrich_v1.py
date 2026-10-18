"""Inject engine identity into map outputs and align audits to assignments."""

from __future__ import annotations

from typing import Any


def _family_lookup(pool_manifest: list[dict[str, Any]]) -> dict[str, str]:
    return {str(m.get("model", "")): str(m.get("family", "")) for m in pool_manifest}


def inject_decontextualizer_model(
    abstract: dict[str, Any],
    decontextualizer_model: str | None,
) -> dict[str, Any]:
    out = dict(abstract)
    if decontextualizer_model:
        out["decontextualizer_model"] = decontextualizer_model
    return out


def abstract_briefs_from_map_ideate(
    map_outputs: list[Any | None],
    pool_models: list[str],
    pool_manifest: list[dict[str, Any]],
    run_id: str,
    briefs_per_model: int,
) -> list[dict[str, Any]]:
    """Inject brief_id, author_model, author_family into abstract brief records."""
    families = _family_lookup(pool_manifest)
    result: list[dict[str, Any]] = []
    for pool_index, output in enumerate(map_outputs):
        if output is None:
            continue
        author_model = str(getattr(output, "model_id", "") or "")
        planned = pool_models[pool_index] if pool_index < len(pool_models) else ""
        author_family = families.get(author_model, "")
        payload = output.json if isinstance(getattr(output, "json", None), dict) else {}
        briefs = list(payload.get("briefs") or [])
        for slot, brief in enumerate(briefs[:briefs_per_model]):
            if not isinstance(brief, dict):
                continue
            enriched = dict(brief)
            enriched["brief_id"] = f"{run_id}-{pool_index}-{slot}"
            enriched["author_model"] = author_model or planned
            enriched["author_family"] = author_family
            result.append(enriched)
    return result


def _parse_abstract_index(brief: dict[str, Any]) -> int | None:
    """Parse a 0-based abstract brief index from model JSON; never treat free text as an id."""
    if "abstract_index" in brief:
        val = brief["abstract_index"]
        if isinstance(val, bool):
            return None
        if isinstance(val, int) and val >= 0:
            return val
        if isinstance(val, str) and val.isdigit():
            return int(val)
        return None
    ref = brief.get("abstract_ref")
    if isinstance(ref, bool):
        return None
    if isinstance(ref, int) and ref >= 0:
        return ref
    if isinstance(ref, str) and ref.isdigit():
        return int(ref)
    return None


def _ideate_pool_index(compact_index: int, output_positions: list[int] | None) -> int:
    if output_positions and compact_index < len(output_positions):
        return int(output_positions[compact_index])
    return compact_index


def briefs_from_re_instantiate(
    map_outputs: list[Any],
    abstract_briefs: list[dict[str, Any]],
    pool_manifest: list[dict[str, Any]],
    *,
    run_id: str,
    ideate_output_positions: list[int] | None = None,
    step: str = "re_instantiate",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve domain briefs to engine brief_id via abstract_index and pool position."""
    families = _family_lookup(pool_manifest)
    abstract_by_id = {str(b.get("brief_id", "")): b for b in abstract_briefs}
    result: list[dict[str, Any]] = []
    parse_failures: list[dict[str, Any]] = []
    for compact_index, output in enumerate(map_outputs):
        if output is None:
            continue
        author_model = str(getattr(output, "model_id", "") or "")
        author_family = families.get(author_model, "")
        pool_index = _ideate_pool_index(compact_index, ideate_output_positions)
        payload = output.json if isinstance(getattr(output, "json", None), dict) else {}
        for brief in list(payload.get("briefs") or []):
            if not isinstance(brief, dict):
                continue
            enriched = dict(brief)
            index = _parse_abstract_index(enriched)
            if index is None:
                parse_failures.append(
                    {
                        "step": step,
                        "iteration_index": compact_index,
                        "model": author_model,
                        "error": "abstract_index missing or not a non-negative integer",
                    }
                )
                continue
            bid = f"{run_id}-{pool_index}-{index}"
            abstract = abstract_by_id.get(bid)
            if not abstract:
                parse_failures.append(
                    {
                        "step": step,
                        "iteration_index": compact_index,
                        "model": author_model,
                        "error": f"abstract_index {index} did not resolve to brief_id {bid!r}",
                    }
                )
                continue
            enriched["brief_id"] = bid
            enriched["abstract_ref"] = bid
            enriched["author_model"] = str(abstract.get("author_model") or author_model)
            enriched["author_family"] = str(abstract.get("author_family") or author_family)
            result.append(enriched)
    return result, parse_failures


def inject_briefs_for_assignment(briefs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure brief_id, author_model, author_family are present on each brief dict."""
    return [dict(b) for b in briefs]


def audits_from_map_audit(
    map_outputs: list[Any | None],
    assignments: list[dict[str, Any]],
    pool_manifest: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy assignment identity onto audit records; one aligned output per assignment."""
    families = _family_lookup(pool_manifest)
    audits: list[dict[str, Any]] = []
    for assignment, output in zip(assignments, map_outputs, strict=False):
        if not isinstance(assignment, dict):
            continue
        if output is None:
            continue
        payload = output.json if isinstance(getattr(output, "json", None), dict) else {}
        if not payload:
            continue
        audit = dict(payload)
        audit["brief_id"] = str(assignment.get("brief_id", ""))
        audit["auditor_model"] = str(assignment.get("auditor_model", ""))
        audit["auditor_family"] = str(
            assignment.get("auditor_family")
            or families.get(str(assignment.get("auditor_model", "")), "")
        )
        audits.append(audit)
    return audits


def model_id_mismatches(
    map_outputs: list[Any | None],
    planned_models: list[str],
    step: str,
) -> list[dict[str, Any]]:
    """Record StepOutput.model_id differing from the planned model for an iteration."""
    failures: list[dict[str, Any]] = []
    for index, (output, planned) in enumerate(zip(map_outputs, planned_models, strict=False)):
        if output is None or not planned:
            continue
        actual = str(getattr(output, "model_id", "") or "")
        if actual and actual != planned:
            failures.append(
                {
                    "step": step,
                    "iteration_index": index,
                    "model": actual,
                    "error": f"model_id mismatch: expected {planned!r}, got {actual!r}",
                }
            )
    return failures


def map_parse_failures_from_collection(
    map_outputs: list[Any | None],
    step: str,
    planned_models: list[str] | None = None,
    failed_hints: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Collect json_parse_error rows and failed_iteration_hints for a map step."""
    failures: list[dict[str, Any]] = []
    for index, output in enumerate(map_outputs):
        if output is None:
            continue
        model = str(getattr(output, "model_id", "") or "")
        if planned_models and index < len(planned_models) and not model:
            model = planned_models[index]
        err = getattr(output, "json_parse_error", None)
        if err:
            failures.append(
                {
                    "step": step,
                    "iteration_index": index,
                    "model": model,
                    "error": str(err),
                }
            )
    for hint in failed_hints or []:
        if not isinstance(hint, dict):
            continue
        idx = int(hint.get("index", -1))
        model = ""
        if planned_models and 0 <= idx < len(planned_models):
            model = planned_models[idx]
        failures.append(
            {
                "step": step,
                "iteration_index": idx,
                "model": model,
                "error": str(hint.get("error_message", "iteration failed")),
            }
        )
    return failures
