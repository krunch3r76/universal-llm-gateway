"""Run manifest assembly, falsifier metric, and halt_reason detection."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from divergent_ideation.schemas import REQUIRED_B_FIELDS, REQUIRED_U_FIELDS


def _normalize_label(label: str) -> str:
    return " ".join(label.casefold().split())


def compute_convergence_rate(
    briefs: list[dict[str, Any]],
    audits: list[dict[str, Any]],
) -> tuple[float, bool, list[dict[str, Any]]]:
    """Return (convergence_rate, falsified, mechanism_clusters)."""
    audits_by_brief: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for audit in audits:
        audits_by_brief[str(audit.get("brief_id", ""))].append(audit)

    brief_labels: dict[str, list[str]] = {}
    eligible: list[str] = []
    for brief in briefs:
        bid = str(brief.get("brief_id", ""))
        author_family = str(brief.get("author_family", ""))
        cross = [
            a
            for a in audits_by_brief.get(bid, [])
            if str(a.get("auditor_family", "")) != author_family
            and a.get("re_instantiation_faithful") is True
        ]
        if not cross:
            continue
        labels = [_normalize_label(str(a.get("mechanism_class", ""))) for a in cross if a.get("mechanism_class")]
        if not labels:
            continue
        counts = Counter(labels)
        max_count = max(counts.values())
        modal = [lab for lab, c in counts.items() if c == max_count]
        brief_labels[bid] = modal
        eligible.append(bid)

    label_to_briefs: dict[str, set[str]] = defaultdict(set)
    for bid, labels in brief_labels.items():
        for lab in labels:
            label_to_briefs[lab].add(bid)

    clusters: list[dict[str, Any]] = []
    for label in sorted(label_to_briefs.keys()):
        bids = sorted(label_to_briefs[label])
        fams = sorted(
            {
                str(b.get("author_family", ""))
                for b in briefs
                if str(b.get("brief_id", "")) in label_to_briefs[label]
            }
        )
        clusters.append({"label": label, "brief_ids": bids, "families": fams})

    total_eligible = len(eligible)
    if total_eligible == 0:
        return 0.0, False, clusters

    best = 0
    for label, bids in label_to_briefs.items():
        fams = {
            str(b.get("author_family", ""))
            for b in briefs
            if str(b.get("brief_id", "")) in bids
        }
        if len(fams) < 2:
            continue
        best = max(best, len(bids))
    rate = best / total_eligible
    return rate, rate >= 0.5, clusters


def _validate_brief(record: dict[str, Any]) -> str | None:
    for field in REQUIRED_B_FIELDS:
        if not record.get(field):
            return f"missing {field}"
    return None


def _validate_audit(record: dict[str, Any]) -> str | None:
    for field in REQUIRED_U_FIELDS:
        if field not in record or record.get(field) in (None, ""):
            return f"missing {field}"
    return None


def _detect_halt(
    skipped_steps: dict[str, bool],
    leak_report: dict[str, Any],
    assignment_error: str | None,
    map_failure: bool,
) -> tuple[str | None, str | None]:
    order = [
        "retrieve_operators",
        "operator_split",
        "map_ideate",
        "re_instantiate",
        "audit_assign",
        "map_audit",
    ]
    halted_at: str | None = None
    for name in order:
        if skipped_steps.get(name):
            halted_at = name
            break
    if halted_at is None:
        return None, None
    if not leak_report.get("passed") and leak_report.get("hits"):
        return halted_at, "leak"
    if assignment_error:
        if "pool_invalid" in assignment_error:
            return halted_at, "pool_invalid"
        return halted_at, "pool_too_small"
    if map_failure:
        return halted_at, "map_failure"
    return halted_at, "condition_error"


def map_success_fraction(failed: int, total: int) -> bool:
    """True when success fraction is below 0.5 (collate map_failure gate)."""
    if total <= 0:
        return False
    success = total - failed
    return (success / total) < 0.5


def build_run_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    """Assemble schema M from collate inputs."""
    parse_failures: list[dict[str, Any]] = list(payload.get("parse_failures") or [])
    briefs = list(payload.get("briefs") or [])
    for brief in briefs:
        if isinstance(brief, dict):
            err = _validate_brief(brief)
            if err:
                parse_failures.append(
                    {
                        "step": "collate",
                        "iteration_index": -1,
                        "model": str(brief.get("author_model", "")),
                        "error": err,
                    }
                )
    audits = list(payload.get("audits") or [])
    for audit in audits:
        if isinstance(audit, dict):
            err = _validate_audit(audit)
            if err:
                parse_failures.append(
                    {
                        "step": "map_audit",
                        "iteration_index": -1,
                        "model": str(audit.get("auditor_model", "")),
                        "error": err,
                    }
                )

    rate, falsified, clusters = compute_convergence_rate(briefs, audits)
    leak_report = dict(payload.get("leak_report") or {})
    assignment_error = payload.get("assignment_error")
    map_failure = bool(payload.get("map_failure"))
    skipped = dict(payload.get("skipped_steps") or {})
    halted_at, halt_reason = _detect_halt(skipped, leak_report, assignment_error, map_failure)

    return {
        "run_id": str(payload.get("run_id", "")),
        "pipeline": "divergent-ideation-v1",
        "problem_id": str(payload.get("problem_id", "")),
        "run_seed": int(payload.get("run_seed") or 0),
        "benchmark_tag": payload.get("benchmark_tag"),
        "pool": list(payload.get("pool_manifest") or []),
        "leak_report": leak_report,
        "operator_dispersion": float(payload.get("operator_dispersion") or 0.0),
        "operator_subsets": list(payload.get("operator_subsets") or []),
        "abstract_briefs": sorted(
            list(payload.get("abstract_briefs") or []),
            key=lambda b: str(b.get("brief_id", "")),
        ),
        "briefs": sorted(briefs, key=lambda b: str(b.get("brief_id", ""))),
        "audit_assignments": sorted(
            list(payload.get("audit_assignments") or []),
            key=lambda x: (x.get("brief_id", ""), x.get("auditor_model", "")),
        ),
        "audits": sorted(audits, key=lambda x: (x.get("brief_id", ""), x.get("auditor_model", ""))),
        "mechanism_clusters": clusters,
        "convergence_rate": rate,
        "falsified": falsified,
        "halted_at": halted_at,
        "halt_reason": halt_reason,
        "parse_failures": parse_failures or None,
    }
