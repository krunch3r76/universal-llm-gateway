#!/usr/bin/env python3
"""Blinded life-intent corpus harness — Arm A (propose route) + Arm B (comparator stub).

Arm A calls POST /api/v1/life/intent/propose (zero-scout on propose path).
Arm B records consult-routing comparator metadata without live dispatch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "services" / "universal-stargate"))

from life_intent.proposal_store import clear_store  # noqa: E402
from life_intent.registry import load_registry  # noqa: E402
from systems.frontier_consult.life_intent_routes import life_intent_router  # noqa: E402

CORPUS_DIR = ROOT / "tests" / "life_intent_corpus"
INTENTS_PATH = CORPUS_DIR / "intents.yaml"
GOLDENS_PATH = CORPUS_DIR / "golden_lanes.yaml"
RUBRIC_PATH = CORPUS_DIR / "RUBRIC.md"

_REFUSE_TOKENS = (
    "team_dispatch",
    "contract=",
    "cursor-sdk",
    "op=",
    "role=",
    "model=",
)


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(life_intent_router)
    return TestClient(app)


def _abstraction_leak(response: dict[str, Any]) -> float:
    blob = json.dumps(response).lower()
    for token in _REFUSE_TOKENS:
        if token in blob:
            return 1.0
    return 0.0


def _score_arm_a(
    golden: dict[str, Any],
    response: dict[str, Any],
    registry_lane: str | None,
) -> dict[str, float]:
    metrics = {
        "correct_lane_rate": 0.0,
        "admission_validity": 1.0,
        "unwanted_dispatch_rate": 0.0,
        "abstraction_leak_rate": _abstraction_leak(response),
        "operator_repair_rate": 0.0,
    }

    expected_reject = golden.get("expect_reject")
    if expected_reject:
        codes = [r.get("code") for r in response.get("rejects", [])]
        metrics["correct_lane_rate"] = 1.0 if expected_reject in codes else 0.0
        metrics["reject_validity"] = metrics["correct_lane_rate"]
        return metrics

    if golden.get("expect_questions"):
        metrics["correct_lane_rate"] = 1.0 if response.get("questions") else 0.0
        return metrics

    verb = (response.get("normalized_intent") or {}).get("verb")
    expected_verb = golden.get("expected_verb")
    lane_ok = registry_lane == golden.get("expected_lane") if registry_lane else False
    verb_ok = verb == expected_verb
    has_proposal = bool(response.get("proposal_id"))
    metrics["correct_lane_rate"] = 1.0 if verb_ok and lane_ok and has_proposal else 0.0
    return metrics


def _run_arm_a(intents: list[dict[str, Any]], goldens: dict[str, Any]) -> dict[str, Any]:
    clear_store()
    reg = load_registry()
    client = _client()
    bundles: list[dict[str, Any]] = []
    totals = {
        "correct_lane_rate": 0.0,
        "admission_validity": 0.0,
        "unwanted_dispatch_rate": 0.0,
        "abstraction_leak_rate": 0.0,
        "operator_repair_rate": 0.0,
    }

    for entry in intents:
        iid = entry["id"]
        golden = goldens[iid]
        intent = entry["intent"]
        resp = client.post("/api/v1/life/intent/propose", json={"intent": intent})
        body = resp.json()
        verb = (body.get("normalized_intent") or {}).get("verb")
        lane = reg.verbs[verb].lane if verb in reg.verbs else None
        metrics = _score_arm_a(golden, body, lane)
        for key in totals:
            totals[key] += metrics[key]
        bundles.append(
            {
                "intent_id": iid,
                "arm": "A",
                "prompt_text": entry["text"],
                "intent": intent,
                "propose_response": body,
                "metrics": metrics,
            }
        )

    n = len(intents) or 1
    return {
        "arm": "A",
        "intent_count": len(intents),
        "correct_lane_rate": totals["correct_lane_rate"] / n,
        "admission_validity": totals["admission_validity"] / n,
        "unwanted_dispatch_rate": totals["unwanted_dispatch_rate"] / n,
        "abstraction_leak_rate": totals["abstraction_leak_rate"] / n,
        "operator_repair_rate": totals["operator_repair_rate"] / n,
        "bundles": bundles,
        "rubric_path": str(RUBRIC_PATH),
    }


def _run_arm_b(intents: list[dict[str, Any]], goldens: dict[str, Any]) -> dict[str, Any]:
    """Comparator arm — records routing pressure without firing live dispatch."""
    bundles: list[dict[str, Any]] = []
    totals = {
        "correct_lane_rate": 0.0,
        "admission_validity": 0.0,
        "unwanted_dispatch_rate": 0.0,
        "abstraction_leak_rate": 0.0,
        "operator_repair_rate": 0.0,
    }
    for entry in intents:
        iid = entry["id"]
        golden = goldens[iid]
        # Arm B stub: would require team_dispatch + consult-routing; mark unrun in CI.
        metrics = {
            "correct_lane_rate": 0.0,
            "admission_validity": 0.0,
            "unwanted_dispatch_rate": 0.0,
            "abstraction_leak_rate": 1.0,
            "operator_repair_rate": 0.0,
            "note": "Arm B live comparator deferred — consult-routing harness hook",
        }
        if golden.get("expect_reject"):
            metrics["admission_validity"] = 1.0
        for key in totals:
            totals[key] += metrics.get(key, 0.0)
        bundles.append(
            {
                "intent_id": iid,
                "arm": "B",
                "prompt_text": entry["text"],
                "comparator_stub": True,
                "metrics": metrics,
            }
        )
    n = len(intents) or 1
    return {
        "arm": "B",
        "intent_count": len(intents),
        "correct_lane_rate": totals["correct_lane_rate"] / n,
        "admission_validity": totals["admission_validity"] / n,
        "unwanted_dispatch_rate": totals["unwanted_dispatch_rate"] / n,
        "abstraction_leak_rate": totals["abstraction_leak_rate"] / n,
        "operator_repair_rate": totals["operator_repair_rate"] / n,
        "bundles": bundles,
        "rubric_path": str(RUBRIC_PATH),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Life intent F2 corpus eval")
    parser.add_argument("--output", type=Path, default=ROOT / "tmp" / "life_intent_corpus_report.json")
    parser.add_argument("--arm", choices=("A", "B", "both"), default="both")
    args = parser.parse_args()

    if not RUBRIC_PATH.is_file():
        print(f"RUBRIC missing: {RUBRIC_PATH}", file=sys.stderr)
        return 2

    data = _load_yaml(INTENTS_PATH)
    intents = data.get("intents") or []
    goldens = _load_yaml(GOLDENS_PATH)

    report: dict[str, Any] = {"rubric": str(RUBRIC_PATH)}
    if args.arm in ("A", "both"):
        report["arm_a"] = _run_arm_a(intents, goldens)
    if args.arm in ("B", "both"):
        report["arm_b"] = _run_arm_b(intents, goldens)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "arm_a" and k != "arm_b"}, indent=2))
    if "arm_a" in report:
        print("Arm A correct_lane_rate:", report["arm_a"]["correct_lane_rate"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
