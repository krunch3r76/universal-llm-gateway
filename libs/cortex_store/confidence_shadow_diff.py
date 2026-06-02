"""§16 shadow-diff — derived band vs the legacy status baseline (no status flip).

CRITICAL framing (thread 1180 turn 5, 1173 cross-ref): the legacy ``entities.status``
baseline is NOT old hand-set ground truth. D-core (the per-write
``substantiation_sync`` hook + migration 049 backfill) already turned it into a
LIVE DERIVED value. So this report compares TWO derivation RULES — D-core's binary
"≥1 confirmed assertion ⇒ confirmed" vs the v1 Φ* + hard confirmed-evidence gate —
NOT derived-vs-truth. A large divergence (especially derived-confirmed ≈ 0 on the
first run, since live ``credibility`` is ~all-NULL ⇒ the §12 external-KB gate passes
for almost nobody) is a RULE DELTA, not a bug. The labels below say so explicitly.

Scope (N4/N5): only entities whose confidence axis is ``status`` and whose status
maps to a confidence band are compared; lifecycle/adoption-valued rows are counted
separately as out-of-scope, never folded into the confusion matrix.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from . import confidence_policy as pol
from .confidence_derivation import DerivationRun

_BANDS = ("unsubstantiated", "provisional", "confirmed")
_MATERIAL_MOVE = 0.05  # |Φ* − b| below which propagation didn't move the score

BASELINE_LABEL = (
    "D-core legacy-derived (per-write substantiation hook + migration 049) "
    "— NOT ground truth"
)
COMPARISON_KIND = "rule-vs-rule: D-core binary gate vs v1 Φ* + hard confirmed gate"


@dataclass
class ShadowDiffReport:
    policy_version: str
    generated_at: str
    baseline_label: str
    comparison_kind: str
    scoped_count: int
    excluded_out_of_scope: int
    excluded_unmapped_status: int
    confusion: dict[str, dict[str, int]]
    exact_agreement_rate: float
    mean_abs_ordinal_delta: float
    derived_confirmed_while_legacy_not: int
    legacy_confirmed_while_derived_not: int
    raw_confirmed_but_gate_failed: int
    confirmed_blocked_by_contradiction: int
    prior_only_count: int
    zero_edge_count: int
    materially_moved_by_propagation: int
    null_credibility_count: int
    notes: list[str] = field(default_factory=list)


def _empty_confusion() -> dict[str, dict[str, int]]:
    return {f"legacy_{lb}": {f"derived_{db}": 0 for db in _BANDS} for lb in _BANDS}


def compute_shadow_diff(run: DerivationRun) -> ShadowDiffReport:
    """Build the §16 report from a ``DerivationRun`` (read-only over its results)."""
    confusion = _empty_confusion()
    scoped = 0
    out_of_scope = 0
    unmapped = 0
    abs_delta_sum = 0
    exact = 0
    derived_conf_legacy_not = 0
    legacy_conf_derived_not = 0
    raw_conf_gate_failed = 0
    conf_blocked_contradiction = 0
    zero_edge = 0
    moved = 0

    for r in run.results.values():
        if not r.in_scope:
            out_of_scope += 1
            continue
        baseline = r.stored_confidence_band
        legacy_band = pol.LEGACY_STATUS_TO_BAND.get(baseline or "")
        if legacy_band is None:
            unmapped += 1  # lifecycle/adoption-valued status — out of confidence scope
            continue

        scoped += 1
        confusion[f"legacy_{legacy_band}"][f"derived_{r.final_band}"] += 1
        l_rank = pol.BAND_RANK[legacy_band]
        d_rank = pol.BAND_RANK[r.final_band]
        abs_delta_sum += abs(d_rank - l_rank)
        if l_rank == d_rank:
            exact += 1
        if r.final_band == "confirmed" and legacy_band != "confirmed":
            derived_conf_legacy_not += 1
        if legacy_band == "confirmed" and r.final_band != "confirmed":
            legacy_conf_derived_not += 1
        if (
            r.raw_band == "confirmed"
            and r.final_band != "confirmed"
            and not r.gate_pass
        ):
            raw_conf_gate_failed += 1
        if r.raw_band == "confirmed" and r.contradiction_cap:
            conf_blocked_contradiction += 1
        if r.zero_edge:
            zero_edge += 1
        elif abs(r.score - r.b_prior) >= _MATERIAL_MOVE:
            moved += 1

    notes = [
        f"Baseline is {COMPARISON_KIND}; do not read the baseline as gold.",
        "derived-confirmed≈0 on first run reflects ~all-NULL credibility ⇒ §12 "
        "external-KB gate unmet (a rule delta, not a defect).",
        "D-core (live baseline) promotes on staged assertions; Φ* applies the §2 "
        "eligibility filter (committed/NULL only) — accounts for some legacy-confirmed-"
        "while-derived-not rows.",
    ]
    return ShadowDiffReport(
        policy_version=run.policy_version,
        generated_at=datetime.datetime.now(tz=datetime.UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        baseline_label=BASELINE_LABEL,
        comparison_kind=COMPARISON_KIND,
        scoped_count=scoped,
        excluded_out_of_scope=out_of_scope,
        excluded_unmapped_status=unmapped,
        confusion=confusion,
        exact_agreement_rate=(exact / scoped) if scoped else 0.0,
        mean_abs_ordinal_delta=(abs_delta_sum / scoped) if scoped else 0.0,
        derived_confirmed_while_legacy_not=derived_conf_legacy_not,
        legacy_confirmed_while_derived_not=legacy_conf_derived_not,
        raw_confirmed_but_gate_failed=raw_conf_gate_failed,
        confirmed_blocked_by_contradiction=conf_blocked_contradiction,
        prior_only_count=run.prior_only_count,
        zero_edge_count=zero_edge,
        materially_moved_by_propagation=moved,
        null_credibility_count=run.null_credibility_count,
        notes=notes,
    )


def render_markdown(report: ShadowDiffReport) -> str:
    """Human-readable §16 report (for sidecars / agent-bus summaries)."""
    lines = [
        "# Confidence shadow-diff (§16) — Phase 1, no status flip",
        f"- policy: `{report.policy_version}` · generated: {report.generated_at}",
        f"- baseline: {report.baseline_label}",
        f"- comparison: **{report.comparison_kind}**",
        f"- scoped: {report.scoped_count} · out-of-scope: {report.excluded_out_of_scope}"
        f" · unmapped-status: {report.excluded_unmapped_status}",
        "",
        "## Confusion (legacy-mapped ↓ vs derived →)",
        "| legacy \\ derived | unsubstantiated | provisional | confirmed |",
        "|---|---:|---:|---:|",
    ]
    for lb in _BANDS:
        row = report.confusion[f"legacy_{lb}"]
        lines.append(
            f"| {lb} | {row['derived_unsubstantiated']} | "
            f"{row['derived_provisional']} | {row['derived_confirmed']} |"
        )
    lines += [
        "",
        f"- exact-agreement: {report.exact_agreement_rate:.3f} · "
        f"mean |ordinal Δ|: {report.mean_abs_ordinal_delta:.3f}",
        f"- derived-confirmed/legacy-not: {report.derived_confirmed_while_legacy_not} · "
        f"legacy-confirmed/derived-not: {report.legacy_confirmed_while_derived_not}",
        f"- raw-confirmed-but-gate-failed: {report.raw_confirmed_but_gate_failed} · "
        f"confirmed-blocked-by-contradiction: {report.confirmed_blocked_by_contradiction}",
        f"- prior-only: {report.prior_only_count} · zero-edge: {report.zero_edge_count}"
        f" · materially-moved-by-propagation: {report.materially_moved_by_propagation}",
        f"- NULL credibility (→ unrated): {report.null_credibility_count}",
        "",
        "## Notes",
        *[f"- {n}" for n in report.notes],
    ]
    return "\n".join(lines)


__all__ = [
    "BASELINE_LABEL",
    "COMPARISON_KIND",
    "ShadowDiffReport",
    "compute_shadow_diff",
    "render_markdown",
]
