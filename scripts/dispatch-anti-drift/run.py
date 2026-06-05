"""Lane B runner — dispatch anti-drift probe suite (G2 anti-drift CI, SoT §10).

Invokes the three Lane B probe modules and emits a JSON probe report.

Usage:
    python scripts/dispatch-anti-drift/run.py [--probe {declarative,behavioral,toolloop,all}]
    python scripts/dispatch-anti-drift/run.py --probe behavioral --live
    python scripts/dispatch-anti-drift/run.py --probe toolloop --models openai/gpt-5.5

This script is NOT a PR gate.  It is intended for:
- Model-add gate: run after adding a model to the registry.
- Scheduled operator runs to catch provider drift.
- Manual investigation via ``manage`` or direct invocation.

Output: JSON probe report written to stdout (and optionally a file via
``--output``). Non-zero exit code if any drift is detected.

[universal:rest] — all HTTP via transport_utils (consumed by probe modules).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

# Allow running from repo root: add libs/ to path if not already present.
_LIBS = Path(__file__).parent.parent.parent / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))

from behavioral_probe import BehavioralReport, run_behavioral_probe  # noqa: E402
from declarative_probe import DeclarativeReport, run_declarative_probe  # noqa: E402
from toolloop_probe import ToolLoopReport, run_toolloop_probe  # noqa: E402

# --------------------------------------------------------------------------- #
# Report serialization
# --------------------------------------------------------------------------- #


def _report_to_dict(
    declarative: DeclarativeReport | None,
    behavioral: BehavioralReport | None,
    toolloop: ToolLoopReport | None,
    elapsed_s: float,
) -> dict:
    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "elapsed_s": round(elapsed_s, 2),
        "summary": {
            "declarative_drift": declarative.drift_count if declarative else None,
            "declarative_passed": declarative.passed() if declarative else None,
            "behavioral_drift": behavioral.drift_count if behavioral else None,
            "behavioral_passed": behavioral.passed() if behavioral else None,
            "toolloop_drift": toolloop.drift_count if toolloop else None,
            "toolloop_passed": toolloop.passed() if toolloop else None,
            "toolloop_golden_anchor_passed": (
                toolloop.golden_anchor_passed() if toolloop else None
            ),
            "overall_passed": _overall_passed(declarative, behavioral, toolloop),
        },
        "declarative": [asdict(f) for f in declarative.findings] if declarative else [],
        "behavioral": [asdict(f) for f in behavioral.findings] if behavioral else [],
        "toolloop": [asdict(f) for f in toolloop.findings] if toolloop else [],
    }


def _overall_passed(
    declarative: DeclarativeReport | None,
    behavioral: BehavioralReport | None,
    toolloop: ToolLoopReport | None,
) -> bool:
    checks = [
        declarative.passed() if declarative else True,
        behavioral.passed() if behavioral else True,
        toolloop.passed() if toolloop else True,
    ]
    return all(checks)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="G2 anti-drift probe runner — emits a JSON report."
    )
    parser.add_argument(
        "--probe",
        choices=["declarative", "behavioral", "toolloop", "all"],
        default="all",
        help="Which probe(s) to run (default: all).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="For behavioral probe: also send live requests to Stargate.",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        help="For toolloop probe: restrict to these model ids.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write JSON report to this file in addition to stdout.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    import time

    t0 = time.monotonic()

    declarative: DeclarativeReport | None = None
    behavioral: BehavioralReport | None = None
    toolloop: ToolLoopReport | None = None

    if args.probe in ("declarative", "all"):
        declarative = run_declarative_probe()

    if args.probe in ("behavioral", "all"):
        behavioral = run_behavioral_probe(live=args.live)

    if args.probe in ("toolloop", "all"):
        toolloop = run_toolloop_probe(models=args.models)

    elapsed_s = time.monotonic() - t0
    report = _report_to_dict(declarative, behavioral, toolloop, elapsed_s)
    output = json.dumps(report, indent=2, default=str)

    print(output)
    if args.output:
        args.output.write_text(output)

    overall = _overall_passed(declarative, behavioral, toolloop)
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
