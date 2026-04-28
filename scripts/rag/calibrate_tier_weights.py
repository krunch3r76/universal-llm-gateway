"""Empirical calibration of tier_weight values for RAG tier-weighted retrieval.

Workflow:
  1. Reindex a small calibration fixture of legal files with provenance_tier tags.
  2. For each candidate tier_weight config, run calibration queries and measure
     MRR (Mean Reciprocal Rank) for Tier-1 citations.
  3. Print recommended defaults with empirical justification.

Usage:
  python scripts/rag/calibrate_tier_weights.py
  python scripts/rag/calibrate_tier_weights.py --skip-reindex   # if fixture already staged
  python scripts/rag/calibrate_tier_weights.py --top-k 20 --json

∀ reindex: force=True so metadata_overrides overwrite the cached chunk metadata.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from transport_utils import DEFAULT_RAG_URL, make_async_client

# ---------------------------------------------------------------------------
# Calibration fixture — (path, provenance_tier)
# ---------------------------------------------------------------------------
# Tier 1 court_record: filed court opinions / orders.
# Tier 1 regulator_pub: statutes, BOE letters, CFPB publications, court forms.
# Tier 2 practitioner_analysis: named-author practitioner articles / legal-aid handbooks.
# Tier 3 expert_commentary: peer-reviewed academic papers.

TIER_FIXTURE: list[tuple[str, str]] = [
    # Tier 1 — court opinions
    (
        "/mnt/torus/mcp-data/files/legal/appellate-opinions/asaro-v-maniscalco-2024-859-trustee.md",
        "court_record",
    ),
    (
        "/mnt/torus/mcp-data/files/legal/foreclosure-hbor/sheen-v-wells-fargo-12-cal-5th-905-2022.pdf",
        "court_record",
    ),
    (
        "/mnt/torus/mcp-data/files/legal/foreclosure-hbor/yvanova-v-new-century-mortgage-62-cal-4th-919-2016.md",
        "court_record",
    ),
    (
        "/mnt/torus/mcp-data/files/legal/appellate-opinions/keading-v-keading-2021-859-elder-abuse.md",
        "court_record",
    ),
    (
        "/mnt/torus/mcp-data/files/legal/appellate-opinions/conservatorship-of-ribal-2019-859-double-damages.md",
        "court_record",
    ),
    # Tier 1 — regulatory publications / statutes
    (
        "/mnt/torus/mcp-data/files/legal/property-tax/boe-lta-2021-008-intergenerational-qa.pdf",
        "regulator_pub",
    ),
    (
        "/mnt/torus/mcp-data/files/legal/property-tax/boe-lta-2022-009-rules-462520-462540.pdf",
        "regulator_pub",
    ),
    (
        "/mnt/torus/mcp-data/files/legal/foreclosure-hbor/civ-2924-15-hbor-scope-applicability.md",
        "regulator_pub",
    ),
    (
        "/mnt/torus/mcp-data/files/legal/foreclosure-hbor/civ-2954-8-escrow-interest-2pct.md",
        "regulator_pub",
    ),
    (
        "/mnt/torus/mcp-data/files/legal/mortgage-servicing/cfpb-mortgage-servicing-exam-procedures-2016.pdf",
        "regulator_pub",
    ),
    (
        "/mnt/torus/mcp-data/files/legal/mortgage-servicing/cfpb-mortgage-servicing-faqs-compliance-aid.md",
        "regulator_pub",
    ),
    # Tier 2 — practitioner analysis
    (
        "/mnt/torus/mcp-data/files/legal/legal-theory/broker-dealer-liability-elder-exploitation-natlawreview.md",
        "practitioner_analysis",
    ),
    (
        "/mnt/torus/mcp-data/files/legal/legal-theory/carlin-umar-yi-2023-deputization-elder-abuse.pdf",
        "practitioner_analysis",
    ),
    # Tier 3 — expert commentary (academic)
    (
        "/mnt/torus/mcp-data/files/legal/legal-theory/egan-matvos-seru-2019-financial-adviser-misconduct.pdf",
        "expert_commentary",
    ),
    (
        "/mnt/torus/mcp-data/files/legal/legal-theory/elder-financial-exploitation-digital-age-jaapl-2023.md",
        "expert_commentary",
    ),
]


@dataclass
class CalibrationQuery:
    query: str
    # Fragment of the source path that must appear in the expected top result
    expected_source_fragment: str
    expected_tier: str
    description: str


CALIBRATION_QUERIES: list[CalibrationQuery] = [
    CalibrationQuery(
        query="Sheen Wells Fargo mortgage servicer tort duty negligence duty of care",
        expected_source_fragment="sheen-v-wells-fargo",
        expected_tier="court_record",
        description="Sheen v. Wells Fargo (Cal. 5th 2022) — Tier 1 court opinion",
    ),
    CalibrationQuery(
        query="HBOR homeowner bill of rights scope applicability first lien single-family Civil Code 2924.15",
        expected_source_fragment="civ-2924-15",
        expected_tier="regulator_pub",
        description="CA Civ. Code § 2924.15 HBOR scope statute — Tier 1 regulator pub",
    ),
    CalibrationQuery(
        query="BOE intergenerational transfer parent child exclusion questions answers Prop 19",
        expected_source_fragment="boe-lta-2021-008",
        expected_tier="regulator_pub",
        description="BOE LTA 2021-008 intergenerational Q&A — Tier 1 regulator pub",
    ),
    CalibrationQuery(
        query="CFPB mortgage servicing examination procedures loss mitigation escrow requirements",
        expected_source_fragment="cfpb-mortgage-servicing-exam-procedures",
        expected_tier="regulator_pub",
        description="CFPB mortgage servicing exam procedures — Tier 1 regulator pub",
    ),
    CalibrationQuery(
        query="Probate Code 859 double damages bad faith trustee order",
        expected_source_fragment="asaro-v-maniscalco",
        expected_tier="court_record",
        description="Asaro v. Maniscalco (2024) §859 trustee — Tier 1 court opinion",
    ),
    CalibrationQuery(
        query="Yvanova wrongful foreclosure standing securitized trust California Supreme Court",
        expected_source_fragment="yvanova",
        expected_tier="court_record",
        description="Yvanova v. New Century Mortgage (Cal. 4th 2016) — Tier 1 court opinion",
    ),
    CalibrationQuery(
        query="escrow impound interest 2 percent paid Civil Code 2954.8 lender obligation",
        expected_source_fragment="civ-2954-8",
        expected_tier="regulator_pub",
        description="CA Civ. Code § 2954.8 escrow 2% interest — Tier 1 regulator pub",
    ),
    CalibrationQuery(
        query="Rules 462.520 462.540 title 18 parent child grandparent grandchild transfer exclusion",
        expected_source_fragment="boe-lta-2022-009",
        expected_tier="regulator_pub",
        description="BOE LTA 2022-009 CCR Rules 462.520/462.540 — Tier 1 regulator pub",
    ),
]

# Candidate tier_weight configurations to sweep
CANDIDATE_CONFIGS: list[tuple[str, dict[str, float]]] = [
    ("baseline", {}),
    (
        "mild_boost",
        {
            "court_record": 0.90,
            "regulator_pub": 0.90,
            "practitioner_analysis": 0.95,
            "expert_commentary": 0.97,
        },
    ),
    (
        "proposed_defaults",
        {
            "court_record": 0.85,
            "regulator_pub": 0.85,
            "practitioner_analysis": 0.92,
            "expert_commentary": 0.95,
        },
    ),
    (
        "strong_boost",
        {
            "court_record": 0.75,
            "regulator_pub": 0.75,
            "practitioner_analysis": 0.88,
            "expert_commentary": 0.92,
        },
    ),
    (
        "t1_only_mild",
        {
            "court_record": 0.85,
            "regulator_pub": 0.85,
        },
    ),
    (
        "t1_only_strong",
        {
            "court_record": 0.75,
            "regulator_pub": 0.75,
        },
    ),
    (
        "asymmetric_strong_court",
        {
            "court_record": 0.70,
            "regulator_pub": 0.82,
            "practitioner_analysis": 0.92,
            "expert_commentary": 0.97,
        },
    ),
]


async def _reindex_fixture(
    client: httpx.AsyncClient,
    fixture: list[tuple[str, str]],
    *,
    verbose: bool = True,
) -> dict[str, str]:
    """Reindex each fixture file with its provenance_tier override.

    Returns a dict mapping path → outcome ("indexed" | "unchanged" | "error: ...").
    ∀ file: force=True so metadata_overrides replace cached chunk metadata.
    """
    outcomes: dict[str, str] = {}
    for path, tier in fixture:
        if not Path(path).exists():
            outcomes[path] = "error: file not found"
            if verbose:
                print(f"  SKIP {Path(path).name} — file not found", flush=True)
            continue
        payload = {
            "path": path,
            "metadata_overrides": {"provenance_tier": tier},
            "force": True,
        }
        try:
            r = await client.post("/reindex", json=payload, timeout=120.0)
            r.raise_for_status()
            result = r.json()
            outcome = "indexed" if not result.get("unchanged") else "unchanged"
            outcomes[path] = outcome
            if verbose:
                print(
                    f"  {outcome.upper()} [{tier}] {Path(path).name} "
                    f"(chunks={result.get('indexed', 0)})",
                    flush=True,
                )
        except Exception as exc:
            outcomes[path] = f"error: {exc}"
            if verbose:
                print(f"  ERROR {Path(path).name}: {exc}", flush=True)
    return outcomes


async def _search(
    client: httpx.AsyncClient,
    query: str,
    *,
    top_k: int,
    tier_weight: dict[str, float] | None,
) -> list[dict]:
    """Run a RAG search and return the list of metadata dicts."""
    payload: dict = {
        "query": query,
        "top_k": top_k,
        "scope": "legal_all",
    }
    if tier_weight:
        payload["tier_weight"] = tier_weight
    r = await client.post("/search", json=payload, timeout=60.0)
    r.raise_for_status()
    data = r.json()
    return data.get("metadata", [])


def _reciprocal_rank(metadatas: list[dict], fragment: str) -> float:
    """Return 1/(rank) for the first result whose source contains fragment, else 0."""
    for rank, meta in enumerate(metadatas, start=1):
        src = meta.get("source", "")
        if fragment in src:
            return 1.0 / rank
    return 0.0


@dataclass
class QueryResult:
    query_desc: str
    config_name: str
    rr: float  # reciprocal rank
    tier_hits: int  # number of results with a provenance_tier tag
    rank: int  # 0 = not found in top_k


@dataclass
class SweepResult:
    config_name: str
    tier_weight: dict[str, float]
    mrr: float
    query_results: list[QueryResult] = field(default_factory=list)


async def _run_sweep(
    client: httpx.AsyncClient,
    queries: list[CalibrationQuery],
    configs: list[tuple[str, dict[str, float]]],
    top_k: int,
) -> list[SweepResult]:
    """Run all configs over all queries and return SweepResult per config."""
    results: list[SweepResult] = []
    for config_name, tier_weight in configs:
        query_results = []
        rrs: list[float] = []
        for q in queries:
            metadatas = await _search(
                client, q.query, top_k=top_k, tier_weight=tier_weight or None
            )
            rr = _reciprocal_rank(metadatas, q.expected_source_fragment)
            rank = next(
                (
                    i + 1
                    for i, m in enumerate(metadatas)
                    if q.expected_source_fragment in m.get("source", "")
                ),
                0,
            )
            tier_hits = sum(1 for m in metadatas if m.get("provenance_tier"))
            rrs.append(rr)
            query_results.append(
                QueryResult(
                    query_desc=q.description,
                    config_name=config_name,
                    rr=rr,
                    tier_hits=tier_hits,
                    rank=rank,
                )
            )
        results.append(
            SweepResult(
                config_name=config_name,
                tier_weight=tier_weight,
                mrr=sum(rrs) / len(rrs) if rrs else 0.0,
                query_results=query_results,
            )
        )
    return results


def _print_report(results: list[SweepResult], queries: list[CalibrationQuery]) -> None:
    baseline = next((r for r in results if r.config_name == "baseline"), None)
    baseline_mrr = baseline.mrr if baseline else 0.0

    print("\n" + "=" * 72)
    print("TIER WEIGHT CALIBRATION RESULTS")
    print("=" * 72)
    print(f"{'Config':<28} {'MRR':>7} {'vs baseline':>12} {'Hits/q':>8}")
    print("-" * 60)
    for r in sorted(results, key=lambda x: -x.mrr):
        delta = r.mrr - baseline_mrr
        avg_hits = (
            sum(qr.tier_hits for qr in r.query_results) / len(r.query_results)
            if r.query_results
            else 0
        )
        sign = "+" if delta >= 0 else ""
        print(
            f"{'*' if r.config_name == 'baseline' else ' '}"
            f"{r.config_name:<27} {r.mrr:.4f} {sign}{delta:+.4f}       {avg_hits:.1f}"
        )

    best = max(results, key=lambda x: x.mrr)
    print(f"\n→ Best config: {best.config_name}  MRR={best.mrr:.4f}")
    print(f"→ Baseline:    {baseline_mrr:.4f}")
    if best.config_name != "baseline":
        improvement = (
            (best.mrr - baseline_mrr) / baseline_mrr * 100 if baseline_mrr else 0
        )
        print(f"→ Improvement: {improvement:+.1f}% vs baseline")

    print("\n--- Per-query rank breakdown (best config) ---")
    baseline_map = (
        {qr.query_desc: qr.rank for qr in baseline.query_results} if baseline else {}
    )
    for qr in best.query_results:
        base_rank = baseline_map.get(qr.query_desc, 0)
        rank_str = f"rank {qr.rank}" if qr.rank > 0 else "not found"
        base_str = f"rank {base_rank}" if base_rank > 0 else "not found"
        delta_str = ""
        if base_rank > 0 and qr.rank > 0:
            delta_str = f" (Δ {base_rank - qr.rank:+d})"
        elif base_rank == 0 and qr.rank > 0:
            delta_str = " (appeared)"
        elif base_rank > 0 and qr.rank == 0:
            delta_str = " (disappeared)"
        print(
            f"  {qr.query_desc[:55]:<55}  base={base_str}  best={rank_str}{delta_str}"
        )

    print("\n--- Recommended tier_weight defaults ---")
    print(f"Config: {best.config_name}")
    print(json.dumps(best.tier_weight, indent=2))
    print("\nEmpirical basis:")
    print(
        f"  Calibration corpus: {len(TIER_FIXTURE)} files  ({sum(1 for _, t in TIER_FIXTURE if t in ('court_record', 'regulator_pub'))} Tier-1, "
        f"{sum(1 for _, t in TIER_FIXTURE if t == 'practitioner_analysis')} Tier-2, "
        f"{sum(1 for _, t in TIER_FIXTURE if t == 'expert_commentary')} Tier-3)"
    )
    print(f"  Calibration queries: {len(queries)}")
    print(f"  Metric: MRR@{TOP_K} over citation-verification queries")
    print(f"  MRR improvement: baseline={baseline_mrr:.4f} → best={best.mrr:.4f}")


async def main(args: argparse.Namespace) -> None:
    async with make_async_client(DEFAULT_RAG_URL, timeout=120.0) as client:
        # 1. Reindex fixture files with their provenance_tier tags
        if not args.skip_reindex:
            print(
                f"Reindexing {len(TIER_FIXTURE)} fixture files with provenance_tier tags..."
            )
            outcomes = await _reindex_fixture(client, TIER_FIXTURE, verbose=True)
            errors = [p for p, o in outcomes.items() if o.startswith("error")]
            if errors:
                print(f"\nWARNING: {len(errors)} file(s) failed to reindex:")
                for p in errors:
                    print(f"  {p}: {outcomes[p]}")
            print(f"Reindex complete. Errors: {len(errors)}/{len(TIER_FIXTURE)}\n")

        # 2. Run calibration sweep
        print(
            f"Running {len(CANDIDATE_CONFIGS)} configs × {len(CALIBRATION_QUERIES)} queries (top_k={args.top_k})..."
        )
        results = await _run_sweep(
            client, CALIBRATION_QUERIES, CANDIDATE_CONFIGS, top_k=args.top_k
        )

        # 3. Report
        if args.json:
            out = [
                {
                    "config_name": r.config_name,
                    "tier_weight": r.tier_weight,
                    "mrr": r.mrr,
                    "query_results": [
                        {
                            "query": qr.query_desc,
                            "rr": qr.rr,
                            "rank": qr.rank,
                            "tier_hits": qr.tier_hits,
                        }
                        for qr in r.query_results
                    ],
                }
                for r in results
            ]
            print(json.dumps(out, indent=2))
        else:
            _print_report(results, CALIBRATION_QUERIES)


TOP_K = 20

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-reindex",
        action="store_true",
        help="Skip the fixture reindex step (use when fixture is already staged)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=TOP_K,
        help=f"Retrieval depth for rank measurement (default {TOP_K})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of human-readable report",
    )
    asyncio.run(main(parser.parse_args()))
