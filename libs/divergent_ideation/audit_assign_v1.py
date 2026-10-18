"""Cross-family auditor assignment (exclusion authority for map_audit)."""

from __future__ import annotations

import hashlib
from typing import Any


def _pool_models_unique(pool_manifest: list[dict[str, Any]]) -> bool:
    models = [str(m.get("model", "")) for m in pool_manifest]
    return len(models) == len(set(models))


def _family_lookup(pool_manifest: list[dict[str, Any]]) -> dict[str, str]:
    return {str(m.get("model", "")): str(m.get("family", "")) for m in pool_manifest}


def assign_auditors(
    briefs: list[dict[str, Any]],
    pool_manifest: list[dict[str, Any]],
    run_seed: int,
    min_auditors: int = 2,
) -> dict[str, Any]:
    """Build audit_assignments (schema X list) or assignment_error."""
    models = [str(m.get("model", "")) for m in pool_manifest]
    if len(pool_manifest) - 1 < min_auditors:
        return {
            "audit_assignments": [],
            "assignment_error": f"pool_too_small: need {min_auditors} non-author auditors",
        }
    if not _pool_models_unique(pool_manifest):
        return {
            "audit_assignments": [],
            "assignment_error": "pool_invalid: duplicate model keys in pool_manifest",
        }
    model_set = set(models)
    families = _family_lookup(pool_manifest)
    ordered_briefs = sorted(briefs, key=lambda b: str(b.get("brief_id", "")))
    for brief in ordered_briefs:
        author = str(brief.get("author_model", ""))
        if author not in model_set:
            return {
                "audit_assignments": [],
                "assignment_error": f"pool_invalid: author_model {author!r} not in pool",
            }

    assignments: list[dict[str, Any]] = []
    for brief in ordered_briefs:
        author = str(brief.get("author_model", ""))
        author_family = str(brief.get("author_family") or families.get(author, ""))
        brief_id = str(brief.get("brief_id", ""))
        candidates = [m for m in pool_manifest if str(m.get("model", "")) != author]

        def sort_key(entry: dict[str, Any]) -> tuple[int, str]:
            fam = str(entry.get("family", ""))
            cross = 0 if fam != author_family else 1
            model = str(entry.get("model", ""))
            digest = hashlib.sha256(f"{run_seed}:{brief_id}:{model}".encode()).hexdigest()
            return (cross, digest)

        candidates.sort(key=sort_key)
        chosen = candidates[:min_auditors]
        if len(chosen) < min_auditors:
            return {
                "audit_assignments": [],
                "assignment_error": f"pool_too_small: brief {brief_id}",
            }
        for entry in chosen:
            auditor = str(entry.get("model", ""))
            assignments.append(
                {
                    "brief_id": brief_id,
                    "author_model": author,
                    "author_family": author_family,
                    "auditor_model": auditor,
                    "auditor_family": str(entry.get("family", "")),
                    "model_ref": auditor,
                    "brief": brief,
                }
            )
    assignments.sort(key=lambda x: (x["brief_id"], x["auditor_model"]))
    return {"audit_assignments": assignments, "assignment_error": None}
