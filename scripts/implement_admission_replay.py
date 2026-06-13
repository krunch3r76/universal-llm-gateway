#!/usr/bin/env python3
"""CLI wrapper for implement admission shadow replay falsifier."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CORPUS = (
    _ROOT
    / "services/universal-stargate/systems/frontier_consult/fixtures/implement_admission/corpus/historical.json"
)
sys.path[:0] = [str(_ROOT / "libs"), str(_ROOT / "services" / "universal-stargate")]

from systems.frontier_consult.shadow_replay import ReplayCase, run_replay


class _StubCortex:
    """Minimal cortex stub for fixture-only corpus runs."""

    def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN003, ARG002
        attrs: dict = {
            "content_hash": "sha256:fixture",
            "acceptance_criteria": ["AC1", "AC2"],
        }
        if entity_id.startswith("plan:"):
            attrs["phases"] = ["phase-1", "phase-2"]
        if entity_id.startswith("plan_phase:"):
            attrs["phase_number"] = 2
        if "threshold" in entity_id:
            attrs["trips_todo_plan_threshold"] = True
        if "bounded" in entity_id or "relay-bounded" in entity_id:
            attrs["files_expected"] = ["a.py", "b.py"]
        return {"id": entity_id, "name": entity_id, "attributes": attrs}


def _load_corpus(path: Path) -> list[ReplayCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "cases" in raw:
        items = raw["cases"]
    elif isinstance(raw, dict) and "source_ref" in raw:
        items = [raw]
    else:
        items = raw
    return [
        ReplayCase(
            source_ref=item["source_ref"],
            legacy_route=item.get("legacy_route") or {},
            legacy_closeout_mutation=item.get("legacy_closeout_mutation"),
            door=item.get("door", "fixture"),
        )
        for item in items
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Implement admission shadow replay")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=_DEFAULT_CORPUS,
        help=f"JSON corpus or fixture dir (default: {_DEFAULT_CORPUS.relative_to(_ROOT)})",
    )
    parser.add_argument("--min-n", type=int, default=150)
    parser.add_argument("--threshold", type=float, default=0.10)
    parser.add_argument("--report", type=Path, help="Write JSON report path")
    parser.add_argument(
        "--fixtures-only",
        action="store_true",
        help="Validate golden fixtures only (does not claim full falsifier pass)",
    )
    args = parser.parse_args(argv)

    if args.corpus.is_dir():
        paths = sorted(args.corpus.glob("*.json"))
        cases: list[ReplayCase] = []
        for p in paths:
            cases.extend(_load_corpus(p))
    else:
        cases = _load_corpus(args.corpus)

    min_n = len(cases) if args.fixtures_only else args.min_n
    cortex = _StubCortex()
    workspaces_root = Path(__file__).resolve().parents[1]
    report = run_replay(
        cases,
        cortex=cortex,
        min_n=min_n,
        threshold=args.threshold,
        workspaces_root=workspaces_root,
    )

    fixture_harness_passed = (
        args.fixtures_only and report.friction_rate <= args.threshold
    )
    full_falsifier_passed = (not args.fixtures_only) and report.passed
    full_falsifier_pending = args.fixtures_only or report.n < 150

    payload = {
        "n": report.n,
        "friction_rate": report.friction_rate,
        "passed": report.passed,
        "classifications": report.classifications,
        "cases": report.cases,
        "fixture_harness_passed": fixture_harness_passed,
        "full_falsifier_passed": full_falsifier_passed,
        "full_falsifier_pending": full_falsifier_pending,
        "fixtures_only": args.fixtures_only,
    }

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if args.fixtures_only:
        if fixture_harness_passed:
            print("PASS (fixture harness — full_falsifier_pending)")
            return 0
        print(
            f"STOP — fixture harness friction_rate {report.friction_rate} exceeds threshold"
        )
        return 1

    if report.n < args.min_n:
        print(f"STOP — n {report.n} below min_n {args.min_n}")
        return 1
    if report.friction_rate > args.threshold:
        print(
            f"STOP — friction_rate {report.friction_rate} exceeds threshold {args.threshold}"
        )
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
