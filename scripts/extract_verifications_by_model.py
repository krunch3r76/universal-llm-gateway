#!/usr/bin/env python3
"""
Extract v4.0 verification verdicts for a specific model from pipeline execution summaries.

Uses fuzzy (substring) matching on model IDs so you can pass e.g. "qwen" or "zyphra"
instead of the full model id. Reads verdicts_by_model from consensus_verify_chain_v4
step outputs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _default_summary_dirs() -> list[Path]:
    """Return candidate directories to search for summaries (first existing with files wins)."""
    candidates: list[Path] = []
    log_dir = os.environ.get("LOG_DIR")
    if log_dir:
        candidates.append(Path(log_dir) / "pipeline_summaries")
    candidates.append(Path("/tmp/logs/universal-stargate/pipeline_summaries"))
    # Fallbacks when running from repo root
    repo = Path(__file__).resolve().parents[1]
    candidates.append(repo / "logs" / "pipeline_summaries")
    candidates.append(
        repo / "services" / "universal-stargate" / "logs" / "pipeline_summaries"
    )
    return candidates


def _find_summary_files(path: Path) -> list[Path]:
    """Collect summary JSON/YAML files from a file or directory (recursive one level)."""
    if path.is_file():
        return [path] if path.suffix in (".json", ".yaml", ".yml") else []
    if not path.is_dir():
        return []
    files: list[Path] = []
    for f in path.iterdir():
        if f.is_file() and f.suffix in (".json", ".yaml", ".yml"):
            files.append(f)
    for sub in path.iterdir():
        if sub.is_dir():
            for f in sub.iterdir():
                if f.is_file() and f.suffix in (".json", ".yaml", ".yml"):
                    files.append(f)
    return sorted(files, key=lambda p: (p.stat().st_mtime, str(p)), reverse=True)


def _load_summary(filepath: Path) -> dict | None:
    """Load a summary from JSON or YAML."""
    try:
        text = filepath.read_text(encoding="utf-8")
    except OSError as e:
        print(f"Warning: could not read {filepath}: {e}", file=sys.stderr)
        return None
    if filepath.suffix == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            print(f"Warning: invalid JSON in {filepath}: {e}", file=sys.stderr)
            return None
    if filepath.suffix in (".yaml", ".yml"):
        try:
            import yaml

            return yaml.safe_load(text)
        except ImportError:
            print(
                "Warning: PyYAML not installed, cannot read YAML summaries",
                file=sys.stderr,
            )
            return None
        except yaml.YAMLError as e:
            print(f"Warning: invalid YAML in {filepath}: {e}", file=sys.stderr)
            return None
    return None


def _match_models(query: str, model_ids: list[str]) -> list[str]:
    """Return model_ids where query matches (case-insensitive substring)."""
    q = query.lower()
    return [m for m in model_ids if q in m.lower()]


def _claim_lookup(step_json: dict) -> dict[str, str]:
    """Build statement_id -> text from verified_facts and rejected_claims."""
    lookup: dict[str, str] = {}
    for key in ("verified_facts", "rejected_claims"):
        for item in step_json.get(key) or []:
            if isinstance(item, dict):
                sid = item.get("statement_id")
                text = item.get("text")
                if sid is not None and text is not None:
                    lookup[str(sid)] = str(text)
    return lookup


def extract_for_model(
    summary: dict,
    model_query: str,
) -> list[dict]:
    """
    Extract verification data for models matching model_query.

    Returns list of dicts: {step_id, model_id, claims: [{statement_id, verdict, text}]}.
    """
    execution = summary.get("execution") or {}
    steps = execution.get("steps") or []
    result: list[dict] = []

    for step in steps:
        step_id = step.get("step_id", "")
        step_type = step.get("step_type", "")
        if step_type != "consensus_verify_chain_v4":
            continue
        j = step.get("json") or {}
        by_model = j.get("verdicts_by_model")
        if not isinstance(by_model, dict):
            continue
        all_model_ids = list(by_model.keys())
        matched = _match_models(model_query, all_model_ids)
        if not matched:
            continue
        lookup = _claim_lookup(j)
        for model_id in matched:
            verdicts = by_model.get(model_id)
            if not isinstance(verdicts, dict):
                continue
            claims = []
            for sid, verdict in verdicts.items():
                text = lookup.get(sid, "(no text)")
                claims.append(
                    {"statement_id": sid, "verdict": bool(verdict), "text": text}
                )
            result.append(
                {
                    "step_id": step_id,
                    "model_id": model_id,
                    "claims": claims,
                }
            )
    return result


def _format_human(extractions: list[dict], model_query: str) -> str:
    lines = [f"# Verifications for model query: {model_query!r}", ""]
    for block in extractions:
        lines.append(f"## Step: {block['step_id']} — Model: {block['model_id']}")
        lines.append("")
        for c in block["claims"]:
            sym = "✓" if c["verdict"] else "✗"
            lines.append(
                f"  {sym} {c['text'][:200]}{'…' if len(c['text']) > 200 else ''}"
            )
        lines.append("")
    return "\n".join(lines)


def _format_json(extractions: list[dict]) -> str:
    return json.dumps(extractions, indent=2, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract v4.0 verification verdicts by model from pipeline summaries (fuzzy model match)."
    )
    parser.add_argument(
        "model",
        type=str,
        help="Model identifier (substring match, e.g. 'qwen', 'zyphra', 'gemma')",
    )
    parser.add_argument(
        "summary_path",
        type=Path,
        nargs="?",
        default=None,
        help="Summary file or directory (default: LOG_DIR/pipeline_summaries or /tmp/.../pipeline_summaries)",
    )
    parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="Output format (default: human)",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="Only list model IDs found in summaries (no extraction)",
    )
    args = parser.parse_args()

    path = args.summary_path
    if path is None:
        files = []
        seen: set[Path] = set()
        for d in _default_summary_dirs():
            if not d.exists():
                continue
            for f in _find_summary_files(d):
                if f not in seen:
                    seen.add(f)
                    files.append(f)
        files = sorted(files, key=lambda p: (p.stat().st_mtime, str(p)), reverse=True)
        if not files:
            print("Error: no summary .json/.yaml files found.", file=sys.stderr)
            print("Searched:", file=sys.stderr)
            for d in _default_summary_dirs():
                if d.exists():
                    n = len(_find_summary_files(d))
                    suffix = " (no .json/.yaml here)" if n == 0 else f" ({n} file(s))"
                else:
                    suffix = " (missing)"
                print(f"  {d}{suffix}", file=sys.stderr)
            print(
                "Summaries are written under <dir>/<pipeline_id>/*.json after a run with save_execution_summary: true.",
                file=sys.stderr,
            )
            print(
                "To use a specific file: python scripts/extract_verifications_by_model.py MODEL /path/to/summary.json",
                file=sys.stderr,
            )
            return 1
    else:
        if not path.exists():
            print(f"Error: path does not exist: {path}", file=sys.stderr)
            return 1
        files = _find_summary_files(path)
        if not files and path.is_dir():
            # Exec dirs (e.g. .../pipeline_id/20260209_130730_xxx/) only have .md when format=detailed; JSON is in parent
            parent_files = _find_summary_files(path.parent)
            if parent_files:
                files = parent_files
        if not files:
            print(
                f"Error: no summary .json/.yaml files under {path}",
                file=sys.stderr,
            )
            if path.is_dir():
                print(
                    "If this exec dir is from an older run, summary.json is now written for new runs. "
                    "Run the pipeline again to get summary.json in the exec dir.",
                    file=sys.stderr,
                )
            return 1

    if args.list_models:
        seen: set[str] = set()
        for f in files:
            data = _load_summary(f)
            if not data:
                continue
            for step in (data.get("execution") or {}).get("steps") or []:
                j = (step.get("json") or {}).get("verdicts_by_model")
                if isinstance(j, dict):
                    seen.update(j.keys())
        for m in sorted(seen):
            print(m)
        return 0

    all_extractions: list[dict] = []
    for f in files:
        data = _load_summary(f)
        if not data:
            continue
        extracted = extract_for_model(data, args.model)
        if extracted:
            for e in extracted:
                e["_source_file"] = str(f)
            all_extractions.extend(extracted)

    if not all_extractions:
        print(
            f"No verification steps matched model query {args.model!r}. "
            "Try --list-models to see available model IDs.",
            file=sys.stderr,
        )
        return 1

    if args.format == "human":
        print(_format_human(all_extractions, args.model))
    else:
        print(_format_json(all_extractions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
