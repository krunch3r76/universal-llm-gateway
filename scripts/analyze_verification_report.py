#!/usr/bin/env python3
"""
Parse verification_report.json and report caught claims and per-model hallucinations.

Usage:
  source ~/.venvs/universal/bin/activate
  python scripts/analyze_verification_report.py <path>

  path: Execution directory (e.g. .../pipeline_id/20260210_142813_xxx/) or
        direct path to verification_report.json.
  If directory: resolves to dir/verification_report.json.

Output (human-readable by default):
  - Per pass/step: rejected (caught) claims with text and per-model votes.
  - Per model: hallucinations — claims that model voted true for but consensus rejected.
  - Use --json for machine-readable summary: {caught, by_model, votes}.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _resolve_report_path(path: Path) -> Path | None:
    """Return path to verification_report.json. None if not found."""
    if path.is_file():
        return path if path.name == "verification_report.json" else None
    if path.is_dir():
        got = path / "verification_report.json"
        return got if got.is_file() else None
    return None


def _load_report(report_path: Path) -> dict[str, Any]:
    """Load and parse verification_report.json."""
    text = report_path.read_text(encoding="utf-8")
    return json.loads(text)


def _analyze_report(
    report: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, list[str]], dict[str, dict[str, dict[str, bool]]]]:
    """
    Single pass over report: build caught list and per-model hallucination list.

    Hallucination: model M voted true for claim C where C ∈ consensus.rejected.

    Returns:
      caught: List of {pass, step_id, statement_id, text, votes: {model: bool}}
      by_model: {model_id: [claim_id, ...]} where claim_id = "step_id:statement_id"
      votes: All votes keyed by step_id -> statement_id -> model -> bool
    """
    caught: list[dict[str, Any]] = []
    by_model: dict[str, list[str]] = {}
    votes_flat: dict[str, dict[str, dict[str, bool]]] = {}

    for p_block in report.get("passes") or []:
        pass_num = p_block.get("pass", 1)
        for step in p_block.get("steps") or []:
            step_id = step.get("step_id", "")
            consensus = step.get("consensus") or {}
            rejected = consensus.get("rejected") or []
            votes_by_model = step.get("votes_by_model") or {}

            votes_flat[step_id] = {}
            for claim in rejected:
                sid = claim.get("statement_id", "")
                text = claim.get("text", "")
                claim_id = f"{step_id}:{sid}"
                per_model: dict[str, bool] = {}
                for model_id, verdicts in votes_by_model.items():
                    if isinstance(verdicts, dict):
                        v = verdicts.get(sid)
                        per_model[str(model_id)] = bool(v) if v is not None else False
                        if v is True:
                            by_model.setdefault(str(model_id), []).append(claim_id)
                caught.append(
                    {
                        "pass": pass_num,
                        "step_id": step_id,
                        "statement_id": sid,
                        "text": text,
                        "votes": per_model,
                    }
                )
                votes_flat[step_id][sid] = per_model

    return caught, by_model, votes_flat


def _format_human(
    caught: list[dict[str, Any]],
    by_model: dict[str, list[str]],
    report: dict[str, Any],
) -> str:
    """Format human-readable output."""
    lines: list[str] = []
    lines.append("# Verification Report Analysis")
    lines.append("")
    lines.append(f"Pipeline: {report.get('pipeline_id', '')}")
    lines.append(f"Execution: {report.get('execution_id', '')}")
    lines.append("")
    lines.append("## Caught (rejected by consensus)")
    lines.append("")
    for c in caught:
        lines.append(f"### Pass {c['pass']} / {c['step_id']} — {c['statement_id']}")
        lines.append("")
        lines.append(f"  Text: {c['text'][:300]}{'…' if len(c['text']) > 300 else ''}")
        lines.append("  Votes:")
        for m, v in sorted(c["votes"].items()):
            sym = "✓" if v else "✗"
            lines.append(f"    {m}: {sym}")
        lines.append("")
    lines.append("## Hallucinations by model (voted true, consensus rejected)")
    lines.append("")
    for m in sorted(by_model.keys()):
        sids = by_model[m]
        lines.append(f"### {m}: {len(sids)} hallucination(s)")
        for sid in sids:
            lines.append(f"  - {sid}")
        lines.append("")
    return "\n".join(lines)


def _format_json_output(
    caught: list[dict[str, Any]],
    by_model: dict[str, list[str]],
    votes_flat: dict[str, dict[str, dict[str, bool]]],
) -> dict[str, Any]:
    """Build machine-readable summary for --json."""
    caught_summary = [
        {
            "pass": c["pass"],
            "step_id": c["step_id"],
            "statement_id": c["statement_id"],
            "text": c["text"],
            "votes": c["votes"],
        }
        for c in caught
    ]
    return {
        "caught": caught_summary,
        "by_model": {m: list(sids) for m, sids in by_model.items()},
        "votes": votes_flat,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parse verification_report.json: caught claims, per-model hallucinations, votes."
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Execution dir or path to verification_report.json",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON summary",
    )
    args = parser.parse_args()

    report_path = _resolve_report_path(args.path)
    if report_path is None:
        print(
            f"Error: {args.path} is not a verification_report.json or dir containing it.",
            file=sys.stderr,
        )
        return 1

    try:
        report = _load_report(report_path)
    except OSError as e:
        print(f"Error: cannot read {report_path}: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON in {report_path}: {e}", file=sys.stderr)
        return 1

    caught, by_model, votes_flat = _analyze_report(report)

    if args.json:
        out = _format_json_output(caught, by_model, votes_flat)
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(_format_human(caught, by_model, report))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
