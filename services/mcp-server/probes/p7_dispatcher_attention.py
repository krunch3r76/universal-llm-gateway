"""P7 — Claude dispatcher attention / interference probe.

Pre-cutover gate for plan:dual-seat-mcp-reliability Phase D.

Measures:
  A. Descriptor invariants: uniqueness, non-overlap, near-neighbor safety
  B. Prompt battery: keyword-match proxy for dispatcher selection accuracy
     (battery data lives in `_p7_battery.py` so this module stays under
     SLOC budget per [quality]).

Design rationale (Hybrid option iii):
  Real LLM calls require a live Claude API session and operator time.
  Descriptor-level invariants are deterministic, cheap, and always-runnable.
  The keyword-battery proxy gives a bounded accuracy estimate without LLM cost.
  Near-neighbor pairs with high keyword overlap flag human-verification candidates.

Gate criteria (pre-D0 cutover):
  PASS     — Part A: no descriptor violations; Part B: ≥ 80% keyword accuracy
  WARN     — Part B: 67-79% accuracy; flag specific prompts for human review
  FAIL     — Part A violation (duplicate names, empty descriptions)
  NEEDS-HUMAN-CALL — near-neighbor pair Jaccard ≥ 0.4 (possible LLM confusion)

Usage:
  python3 services/mcp-server/probes/p7_dispatcher_attention.py [--json]

P10 note: the ≤24 cap is already asserted by derive_claude_manifest() at init
time and tested in test_route_claude.py::test_claude_primary_tools_count.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
MCP_SERVER_DIR = REPO_ROOT / "services" / "mcp-server"
CANONICAL_YAML = REPO_ROOT / "config" / "mcp" / "canonical.yaml"

sys.path.insert(0, str(MCP_SERVER_DIR))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-noop")
os.environ.setdefault("MCP_OAUTH_DISABLED", "1")

from _p7_battery import _PROMPT_BATTERY, _STOP  # noqa: E402


def _tokenize(text: str) -> set[str]:
    """Lowercase word set, stopwords removed."""
    words = re.findall(r"[a-z_]+", text.lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def _domain_keywords(entry: dict) -> set[str]:
    """Build keyword set for a domain entry."""
    kw: set[str] = set()
    kw.add(entry["domain"])
    kw.add(entry["tool_name"])
    for op in entry.get("ops", []):
        kw.update(op.replace("_", " ").split())
        kw.add(op)
    kw.update(_tokenize(entry.get("description", "")))
    return kw - _STOP


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


# ── Part A: descriptor invariants ────────────────────────────────────────────


_GENERIC_OPS = frozenset(
    {"run", "query", "search", "list", "get", "read", "write", "append", "delete"}
)


def check_descriptor_invariants(manifest: list[dict]) -> list[str]:
    """Return list of violation strings (empty = PASS)."""
    violations: list[str] = []
    names = [e["tool_name"] for e in manifest]
    if len(names) != len(set(names)):
        dup = [n for n, c in Counter(names).items() if c > 1]
        violations.append(f"Duplicate tool_names: {dup}")
    for e in manifest:
        if not e.get("description", "").strip():
            violations.append(f"Empty description for domain={e['domain']!r}")
    op_to_domains: dict[str, list[str]] = {}
    for e in manifest:
        for op in e.get("ops", []):
            op_to_domains.setdefault(op, []).append(e["domain"])
    for op, domains in op_to_domains.items():
        if len(domains) > 1 and op not in _GENERIC_OPS:
            violations.append(
                f"Non-generic op {op!r} appears in multiple domains: {domains}"
            )
    return violations


def check_near_neighbors(manifest: list[dict]) -> list[dict]:
    """Return pairs with Jaccard similarity ≥ 0.25 (flag; ≥ 0.4 = NEEDS-HUMAN-CALL)."""
    kw_map = {e["domain"]: _domain_keywords(e) for e in manifest}
    flags: list[dict] = []
    domains = sorted(kw_map.keys())
    for i, d1 in enumerate(domains):
        for d2 in domains[i + 1 :]:
            j = _jaccard(kw_map[d1], kw_map[d2])
            if j >= 0.25:
                flags.append(
                    {
                        "domain_a": d1,
                        "domain_b": d2,
                        "jaccard": round(j, 3),
                        "shared_keywords": sorted(kw_map[d1] & kw_map[d2]),
                        "level": "NEEDS-HUMAN-CALL" if j >= 0.4 else "WARN",
                    }
                )
    return flags


# ── Part B: prompt battery ────────────────────────────────────────────────────


def run_prompt_battery(manifest: list[dict]) -> dict:
    """Score prompts via keyword overlap. Returns summary dict."""
    kw_map = {e["domain"]: _domain_keywords(e) for e in manifest}
    results = []
    for prompt, expected, notes in _PROMPT_BATTERY:
        prompt_words = _tokenize(prompt)
        scores = {
            d: len(prompt_words & kw) / max(len(prompt_words), 1)
            for d, kw in kw_map.items()
        }
        best = max(scores, key=lambda d: scores[d])
        top2 = sorted(scores.items(), key=lambda x: -x[1])[:2]
        ambiguous = (
            top2[1][1] >= 0.5 * top2[0][1]
            if len(top2) > 1 and top2[0][1] > 0
            else False
        )
        results.append(
            {
                "prompt": prompt,
                "expected": expected,
                "predicted": best,
                "score_winner": round(top2[0][1], 3),
                "score_runner_up": round(top2[1][1], 3) if len(top2) > 1 else 0,
                "correct": best == expected,
                "ambiguous": ambiguous,
                "notes": notes,
            }
        )
    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    ambiguous = sum(1 for r in results if r["ambiguous"])
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total if total else 0.0, 3),
        "ambiguous_count": ambiguous,
        "results": results,
    }


# ── Main ──────────────────────────────────────────────────────────────────────


def _verdict(part_a_pass: bool, nn_needs_human: bool, accuracy: float) -> str:
    if not part_a_pass:
        return "FAIL"
    if nn_needs_human:
        return "NEEDS-HUMAN-CALL"
    if accuracy >= 0.80:
        return "PASS"
    if accuracy >= 0.67:
        return "WARN"
    return "FAIL"


def run_p7(json_output: bool = False) -> int:
    """Run P7 probe. Returns 0 on PASS, 1 on FAIL/NEEDS-HUMAN-CALL."""
    from _derive import derive_claude_manifest  # noqa: PLC0415

    manifest = derive_claude_manifest(CANONICAL_YAML)
    violations = check_descriptor_invariants(manifest)
    near_neighbors = check_near_neighbors(manifest)
    battery = run_prompt_battery(manifest)

    nn_needs_human = any(n["level"] == "NEEDS-HUMAN-CALL" for n in near_neighbors)
    verdict = _verdict(not violations, nn_needs_human, battery["accuracy"])

    report = {
        "probe": "P7",
        "verdict": verdict,
        "manifest_domain_count": len(manifest),
        "part_a": {"violations": violations, "near_neighbor_flags": near_neighbors},
        "part_b": battery,
        "gate_criteria": {
            "part_a_pass_threshold": "0 violations",
            "near_neighbor_needs_human": "Jaccard ≥ 0.4",
            "accuracy_pass": "≥ 0.80",
            "accuracy_warn": "0.67–0.79",
        },
    }

    if json_output:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)
    return 0 if verdict in ("PASS", "WARN") else 1


def _print_report(r: dict) -> None:
    print("\n=== P7 Dispatcher Attention/Interference Probe ===")
    print(f"Verdict: {r['verdict']}  Manifest: {r['manifest_domain_count']} domains")
    a = r["part_a"]
    print("\n--- Part A: Descriptor Invariants ---")
    if a["violations"]:
        print("VIOLATIONS:")
        for v in a["violations"]:
            print(f"  ✗ {v}")
    else:
        print("  ✓ No violations")
    for n in a["near_neighbor_flags"]:
        mark = "⚠" if n["level"] == "WARN" else "🛑"
        print(
            f"  {mark} {n['domain_a']} <-> {n['domain_b']}: "
            f"Jaccard={n['jaccard']} [{n['level']}]  shared={n['shared_keywords']}"
        )
    b = r["part_b"]
    print(
        f"\n--- Part B: Prompt Battery ---\n"
        f"Accuracy: {b['correct']}/{b['total']} ({b['accuracy']:.0%})  "
        f"Ambiguous: {b['ambiguous_count']}"
    )
    for res in b["results"]:
        mark = "✓" if res["correct"] else "✗"
        amb = " [AMB]" if res["ambiguous"] else ""
        print(
            f"  {mark} {res['prompt'][:55]:<55} "
            f"expected={res['expected']} got={res['predicted']}{amb}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="P7 dispatcher attention probe")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()
    sys.exit(run_p7(json_output=args.json))
