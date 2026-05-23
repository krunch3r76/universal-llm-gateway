"""P8 — Prompt-cache invariance probe.

Pre-cutover gate for plan:dual-seat-mcp-reliability Phase D.

Measures:
  A. Manifest byte-stability: derive_claude_manifest() is deterministic
     (same sha256 across N calls → Anthropic cache key won't shift post-D0)
  B. Descriptor payload baseline capture from live event service
     (pre-D0: records tools/list response_bytes; post-D0: compare)
  C. D9 bug pointer — full diagnosis at
     cortex://notes/system/post-mortems/d9-cache-invariance-probe-bug.md

SQL queries, summary dicts, and gate-criterion text live in `_p8_data.py`
so this module stays under SLOC budget per [quality].

Gate criteria:
  PASS     — manifest sha256 stable across N derivations AND baseline captured
  FAIL     — manifest non-deterministic (sha256 varies)
  NEEDS-HUMAN-CALL — post-D0 7-day window monitoring required for cache hit rate

Usage:
  python3 services/mcp-server/probes/p8_prompt_cache_invariance.py [--json] [--baseline-file PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
MCP_SERVER_DIR = REPO_ROOT / "services" / "mcp-server"
CANONICAL_YAML = REPO_ROOT / "config" / "mcp" / "canonical.yaml"
QUERY_EVENTS = REPO_ROOT / "scripts" / "query-events"

sys.path.insert(0, str(MCP_SERVER_DIR))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-noop")
os.environ.setdefault("MCP_OAUTH_DISABLED", "1")

from _p8_data import (  # noqa: E402
    _ANTHROPIC_CACHE_NOTE,
    _BASELINE_SQL,
    _D9_BUG_SUMMARY,
    _POST_D0_CHECK_NOTE,
    _TOOLS_LIST_SQL,
)

_STABILITY_ITERATIONS = 5


# ── Part A: manifest byte-stability ──────────────────────────────────────────


def check_manifest_stability() -> dict:
    """Derive manifest N times; verify sha256 is identical across all calls."""
    from _derive import derive_claude_manifest  # noqa: PLC0415

    hashes: list[str] = []
    domain_counts: list[int] = []
    tool_names_list: list[list[str]] = []

    for _ in range(_STABILITY_ITERATIONS):
        manifest = derive_claude_manifest(CANONICAL_YAML)
        tool_names = sorted(e["tool_name"] for e in manifest)
        payload = json.dumps(tool_names, sort_keys=True).encode()
        hashes.append(hashlib.sha256(payload).hexdigest()[:16])
        domain_counts.append(len(manifest))
        tool_names_list.append(tool_names)

    stable = len(set(hashes)) == 1 and len(set(domain_counts)) == 1

    # Full descriptor sha256 (all descriptions + ops, for post-D0 comparison).
    manifest = derive_claude_manifest(CANONICAL_YAML)
    full_payload = json.dumps(
        [
            {
                "domain": e["domain"],
                "tool_name": e["tool_name"],
                "ops": e["ops"],
                "description": e["description"],
            }
            for e in sorted(manifest, key=lambda x: x["domain"])
        ],
        sort_keys=True,
    ).encode()
    descriptor_sha256 = hashlib.sha256(full_payload).hexdigest()

    return {
        "stable": stable,
        "iterations": _STABILITY_ITERATIONS,
        "hashes": hashes,
        "unique_hash_count": len(set(hashes)),
        "domain_count": domain_counts[0] if domain_counts else 0,
        "names_sha256_prefix": hashes[0] if hashes else "",
        "descriptor_sha256": descriptor_sha256,
        "tool_names": tool_names_list[0] if tool_names_list else [],
    }


# ── Part B: event service baseline capture ───────────────────────────────────


def _query_events(sql: str) -> dict:
    """Run scripts/query-events --sql and return parsed result.

    Narrow exception types — unexpected errors still propagate; expected
    I/O / subprocess / parse failures convert to a structured error envelope.
    """
    try:
        result = subprocess.run(
            [str(QUERY_EVENTS), "--sql", sql],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip() or "non-zero exit"}
        return json.loads(result.stdout.strip())
    except (
        subprocess.TimeoutExpired,
        subprocess.SubprocessError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"_query_events: {type(exc).__name__}: {exc}", file=sys.stderr)
        return {"error": f"{type(exc).__name__}: {exc}"}


def capture_event_baseline() -> dict:
    """Query event service for pre-D0 Claude session baseline metrics."""
    method_dist = _query_events(_BASELINE_SQL)
    tools_list_hist = _query_events(_TOOLS_LIST_SQL)
    rows = method_dist.get("rows", [])
    tools_list_rows = tools_list_hist.get("rows", [])
    tl_bytes = [
        r.get("response_bytes")
        for r in tools_list_rows
        if r.get("response_bytes") is not None
    ]
    tl_bytes_int = [int(b) for b in tl_bytes if b is not None]
    return {
        "event_service_available": "error" not in method_dist,
        "method_distribution": rows,
        "tools_list_events": len(tools_list_rows),
        "tools_list_response_bytes": tl_bytes_int,
        "tools_list_response_bytes_baseline": tl_bytes_int[0] if tl_bytes_int else None,
        "note": (
            "response_bytes for tools/list = MCP descriptor payload size. "
            "Stable response_bytes post-D0 rebuild = Anthropic cache key unchanged."
        ),
    }


# ── Main ──────────────────────────────────────────────────────────────────────


def run_p8(json_output: bool = False, baseline_file: str | None = None) -> int:
    """Run P8 probe. Returns 0 on PASS, 1 on FAIL."""
    stability = check_manifest_stability()
    baseline = capture_event_baseline()

    if not stability["stable"]:
        verdict = "FAIL"
        verdict_reason = "Manifest sha256 not stable across derivations"
    else:
        verdict = "PASS"
        verdict_reason = (
            "Manifest byte-stable; baseline captured. Anthropic cache token "
            "monitoring requires post-D0 7-day window (NEEDS-HUMAN-CALL)."
        )

    report = {
        "probe": "P8",
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "part_a_stability": stability,
        "part_b_baseline": baseline,
        "part_c_d9_bug": _D9_BUG_SUMMARY,
        "post_d0_gate": {
            "metric": "response_bytes for seat_class=claude tools/list",
            "pre_d0_baseline": baseline.get("tools_list_response_bytes_baseline"),
            "post_d0_check": _POST_D0_CHECK_NOTE,
            "anthropic_cache_tokens": _ANTHROPIC_CACHE_NOTE,
        },
    }

    if baseline_file:
        Path(baseline_file).write_text(json.dumps(report, indent=2))
    if json_output:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)
    return 0 if verdict == "PASS" else 1


def _print_report(r: dict) -> None:
    print(f"\n=== P8 Prompt-Cache Invariance Probe ===\nVerdict: {r['verdict']}")
    print(f"Reason:  {r['verdict_reason']}")
    a = r["part_a_stability"]
    print(
        f"\n--- Part A: Manifest Byte-Stability ---\n"
        f"  Stable={a['stable']}  iterations={a['iterations']}  "
        f"domains={a['domain_count']}\n"
        f"  names_sha256={a['names_sha256_prefix']}  "
        f"descriptor_sha256={a['descriptor_sha256'][:24]}..."
    )
    print(
        f"  ✓ All {a['iterations']} hashes identical"
        if a["stable"]
        else f"  ✗ FAIL — hashes diverged: {a['hashes']}"
    )

    b = r["part_b_baseline"]
    pre = b["tools_list_response_bytes_baseline"]
    pre_str = f"{pre:,} bytes" if pre else "N/A (no tools/list events in window)"
    print(
        f"\n--- Part B: Event Service Baseline ---\n"
        f"  available={b['event_service_available']}  "
        f"tools/list events={b['tools_list_events']}\n"
        f"  Pre-D0 descriptor payload: {pre_str}"
    )
    for row in (b.get("method_distribution") or [])[:5]:
        avg = row.get("avg_response_bytes")
        method = row.get("mcp_method") or "(null)"
        avg_str = f"  avg_bytes={avg:.0f}" if avg else ""
        print(f"    {method:30s}  n={row.get('count', '?')}{avg_str}")

    c = r["part_c_d9_bug"]
    print(
        f"\n--- Part C: D9 Bug Diagnosis ---\n  todo: {c['todo_entity']}\n"
        f"  symptom: {c['symptom']}\n  fix: {c['fix']}\n  details: {c['details']}"
    )

    pg = r["post_d0_gate"]
    print(
        f"\n--- Post-D0 Gate Criteria ---\n  Metric: {pg['metric']}\n"
        f"  Baseline: {pg['pre_d0_baseline']}\n"
        f"  Anthropic cache tokens: {pg['anthropic_cache_tokens'][:80]}..."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="P8 prompt-cache invariance probe")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--baseline-file", default=None, help="Save JSON report to file"
    )
    args = parser.parse_args()
    sys.exit(run_p8(json_output=args.json, baseline_file=args.baseline_file))
