#!/usr/bin/env python3
"""Benchmark consult workflows for cost/latency/relevance comparison.

Runs a fixed task suite through baseline and candidate command templates,
captures outputs, parses lightweight metrics, and writes JSON + markdown reports.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import statistics
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

try:
    import yaml
except ImportError as exc:  # pragma: no cover - runtime environment concern
    raise SystemExit("PyYAML is required. Install with: pip install pyyaml") from exc


DEFAULT_FIXTURE = "tasks/specs/consult-code-rag-benchmark-tasks.yaml"
DEFAULT_BASELINE_CMD = (
    "python scripts/consult -r planner --cloud-only --chain -o {output} {prompt}"
)
DEFAULT_CANDIDATE_CMD = (
    "python scripts/consult -r planner --cloud-only --chain --scope source "
    "-o {output} {prompt}"
)

MODEL_HEADING_RE = re.compile(r"^## (?:Phase \d+: [^-]+ — )?(.+)$")
TOKENS_RE = re.compile(r"\*(\d+)ms,\s+(\d+) tokens \((\d+)\+(\d+)\)\*")


@dataclass(slots=True, kw_only=True)
class Task:
    id: str
    domain: str
    category: str
    prompt: str


@dataclass(slots=True, kw_only=True)
class RunMetrics:
    exit_code: int
    duration_ms: int
    cloud_call_count: int
    prompt_tokens_total: int
    completion_tokens_total: int
    total_tokens: int
    context_files_count: int
    grounded_files_count: int
    grounded_ratio: float | None
    churn_count: int
    retry_count: int
    manual_intervention_count: int
    fallback_used: bool
    quality_score: float | None


@dataclass(slots=True, kw_only=True)
class TaskRun:
    task_id: str
    variant: str
    command: str
    output_path: str
    stdout_path: str
    stderr_path: str
    metrics: RunMetrics
    error: str | None = None


@dataclass(slots=True, kw_only=True)
class BenchArgs:
    fixture: str
    baseline_cmd: str
    candidate_cmd: str
    mode: str
    task_ids: list[str] | None
    ratings_json: str | None
    churn_json: str | None
    out_dir: str | None
    dry_run: bool


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark baseline vs code-RAG consult workflows."
    )
    _ = parser.add_argument(
        "--fixture", default=DEFAULT_FIXTURE, help="Path to tasks YAML"
    )
    _ = parser.add_argument(
        "--baseline-cmd",
        default=DEFAULT_BASELINE_CMD,
        help="Baseline command template with placeholders",
    )
    _ = parser.add_argument(
        "--candidate-cmd",
        default=DEFAULT_CANDIDATE_CMD,
        help="Candidate command template with placeholders",
    )
    _ = parser.add_argument(
        "--mode",
        choices=["baseline", "candidate", "both"],
        default="both",
        help="Which workflow variant(s) to run",
    )
    _ = parser.add_argument(
        "--task-ids",
        nargs="*",
        default=None,
        help="Optional subset of task IDs from fixture",
    )
    _ = parser.add_argument(
        "--ratings-json",
        default=None,
        help="Optional JSON file mapping task_id -> {baseline,candidate} score",
    )
    _ = parser.add_argument(
        "--churn-json",
        default=None,
        help=(
            "Optional JSON file mapping task_id -> {baseline,candidate} -> "
            "{retry_count, manual_intervention_count, fallback_used, churn_count}"
        ),
    )
    _ = parser.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: tmp/consult-benchmark/<timestamp>)",
    )
    _ = parser.add_argument(
        "--dry-run", action="store_true", help="Print commands only"
    )
    return parser


def _load_tasks(path: Path) -> list[Task]:
    payload_obj = cast(object, yaml.safe_load(path.read_text()))
    payload: Mapping[str, object]
    if payload_obj is None:
        payload = {}
    elif isinstance(payload_obj, Mapping):
        payload = cast(Mapping[str, object], payload_obj)
    else:
        raise ValueError(f"Invalid fixture payload in {path}: expected mapping root")
    rows_obj = payload.get("tasks", [])
    if not isinstance(rows_obj, list):
        raise ValueError(f"Invalid fixture tasks in {path}: expected list")
    rows = cast(list[object], rows_obj)
    tasks: list[Task] = []
    for row_obj in rows:
        if not isinstance(row_obj, Mapping):
            continue
        row = cast(Mapping[str, object], row_obj)
        task_id = row.get("id")
        domain = row.get("domain")
        category = row.get("category")
        prompt = row.get("prompt")
        if task_id is None or domain is None or category is None or prompt is None:
            continue
        tasks.append(
            Task(
                id=str(task_id),
                domain=str(domain),
                category=str(category),
                prompt=str(prompt).strip(),
            )
        )
    return tasks


def _load_ratings(path: Path | None) -> dict[str, dict[str, float]]:
    if path is None:
        return {}
    raw_obj = cast(object, json.loads(path.read_text()))
    if not isinstance(raw_obj, Mapping):
        raise ValueError(f"Invalid ratings payload in {path}: expected object")
    raw = cast(Mapping[object, object], raw_obj)
    ratings: dict[str, dict[str, float]] = {}
    for task_id_obj, value_obj in raw.items():
        if not isinstance(value_obj, Mapping):
            continue
        value = cast(Mapping[str, object], value_obj)
        parsed: dict[str, float] = {}
        for variant in ("baseline", "candidate"):
            score = value.get(variant)
            if isinstance(score, int | float):
                parsed[variant] = float(score)
        ratings[str(task_id_obj)] = parsed
    return ratings


def _load_churn(path: Path | None) -> dict[str, dict[str, dict[str, object]]]:
    if path is None:
        return {}
    raw_obj = cast(object, json.loads(path.read_text()))
    if not isinstance(raw_obj, Mapping):
        raise ValueError(f"Invalid churn payload in {path}: expected object")
    raw = cast(Mapping[object, object], raw_obj)

    result: dict[str, dict[str, dict[str, object]]] = {}
    for task_id_obj, task_value_obj in raw.items():
        if not isinstance(task_value_obj, Mapping):
            continue
        task_value = cast(Mapping[object, object], task_value_obj)
        by_variant: dict[str, dict[str, object]] = {}
        for variant in ("baseline", "candidate"):
            variant_raw = task_value.get(variant)
            if not isinstance(variant_raw, Mapping):
                continue
            variant_value = cast(Mapping[object, object], variant_raw)
            parsed: dict[str, object] = {}
            retry_count = variant_value.get("retry_count")
            manual_count = variant_value.get("manual_intervention_count")
            fallback_used = variant_value.get("fallback_used")
            churn_count = variant_value.get("churn_count")
            if isinstance(retry_count, int):
                parsed["retry_count"] = retry_count
            if isinstance(manual_count, int):
                parsed["manual_intervention_count"] = manual_count
            if isinstance(fallback_used, bool):
                parsed["fallback_used"] = fallback_used
            if isinstance(churn_count, int):
                parsed["churn_count"] = churn_count
            by_variant[variant] = parsed
        result[str(task_id_obj)] = by_variant
    return result


def _parse_args(parser: argparse.ArgumentParser) -> BenchArgs:
    raw_args: dict[str, object] = vars(parser.parse_args())

    fixture = raw_args.get("fixture")
    baseline_cmd = raw_args.get("baseline_cmd")
    candidate_cmd = raw_args.get("candidate_cmd")
    mode = raw_args.get("mode")
    ratings_json = raw_args.get("ratings_json")
    churn_json = raw_args.get("churn_json")
    out_dir = raw_args.get("out_dir")
    dry_run = raw_args.get("dry_run")
    task_ids_raw = raw_args.get("task_ids")

    if not isinstance(fixture, str):
        raise ValueError("--fixture must be a string")
    if not isinstance(baseline_cmd, str):
        raise ValueError("--baseline-cmd must be a string")
    if not isinstance(candidate_cmd, str):
        raise ValueError("--candidate-cmd must be a string")
    if not isinstance(mode, str):
        raise ValueError("--mode must be a string")

    task_ids: list[str] | None = None
    if isinstance(task_ids_raw, list):
        task_id_values = cast(list[object], task_ids_raw)
        task_ids = [str(task_id) for task_id in task_id_values]
    return BenchArgs(
        fixture=fixture,
        baseline_cmd=baseline_cmd,
        candidate_cmd=candidate_cmd,
        mode=mode,
        task_ids=task_ids,
        ratings_json=ratings_json if isinstance(ratings_json, str) else None,
        churn_json=churn_json if isinstance(churn_json, str) else None,
        out_dir=out_dir if isinstance(out_dir, str) else None,
        dry_run=bool(dry_run),
    )


def _render_command(template: str, task: Task, output_path: Path) -> str:
    return (
        template.replace("{prompt}", shlex.quote(task.prompt))
        .replace("{output}", shlex.quote(str(output_path)))
        .replace("{task_id}", shlex.quote(task.id))
        .replace("{domain}", shlex.quote(task.domain))
        .replace("{category}", shlex.quote(task.category))
    )


def _extract_context_files(markdown: str) -> list[str]:
    for line in markdown.splitlines():
        if line.startswith("**Context files**:"):
            raw = line.split(":", 1)[1].strip()
            if not raw:
                return []
            return [p.strip() for p in raw.split(",") if p.strip()]
    return []


def _parse_metrics(
    *,
    markdown: str,
    duration_ms: int,
    exit_code: int,
    churn_data: Mapping[str, object] | None,
    quality_score: float | None,
) -> RunMetrics:
    models: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0

    for line in markdown.splitlines():
        model_match = MODEL_HEADING_RE.match(line)
        if model_match:
            models.append(model_match.group(1).strip())
            continue
        token_match = TOKENS_RE.search(line)
        if token_match:
            prompt_tokens += int(token_match.group(3))
            completion_tokens += int(token_match.group(4))

    context_files = _extract_context_files(markdown)
    grounded = 0
    for file_path in context_files:
        if Path(file_path).exists():
            grounded += 1
    grounded_ratio: float | None = None
    if context_files:
        grounded_ratio = grounded / len(context_files)

    cloud_calls = sum(1 for m in models if "/" in m)
    total_tokens = prompt_tokens + completion_tokens
    retry_count = 0
    manual_intervention_count = 0
    fallback_used = False
    churn_count = 0
    if churn_data is not None:
        retry_raw = churn_data.get("retry_count")
        manual_raw = churn_data.get("manual_intervention_count")
        fallback_raw = churn_data.get("fallback_used")
        churn_raw = churn_data.get("churn_count")
        if isinstance(retry_raw, int):
            retry_count = retry_raw
        if isinstance(manual_raw, int):
            manual_intervention_count = manual_raw
        if isinstance(fallback_raw, bool):
            fallback_used = fallback_raw
        if isinstance(churn_raw, int):
            churn_count = churn_raw
        else:
            churn_count = retry_count + manual_intervention_count + int(fallback_used)

    return RunMetrics(
        exit_code=exit_code,
        duration_ms=duration_ms,
        cloud_call_count=cloud_calls,
        prompt_tokens_total=prompt_tokens,
        completion_tokens_total=completion_tokens,
        total_tokens=total_tokens,
        context_files_count=len(context_files),
        grounded_files_count=grounded,
        grounded_ratio=grounded_ratio,
        churn_count=churn_count,
        retry_count=retry_count,
        manual_intervention_count=manual_intervention_count,
        fallback_used=fallback_used,
        quality_score=quality_score,
    )


def _run_task_variant(
    *,
    task: Task,
    variant: str,
    template: str,
    run_root: Path,
    churn_data: Mapping[str, object] | None,
    quality_score: float | None,
    dry_run: bool,
) -> TaskRun:
    raw_dir = run_root / "raw" / variant
    raw_dir.mkdir(parents=True, exist_ok=True)
    output_path = raw_dir / f"{task.id}.md"
    stdout_path = raw_dir / f"{task.id}.stdout.log"
    stderr_path = raw_dir / f"{task.id}.stderr.log"
    command = _render_command(template, task, output_path)

    if dry_run:
        metrics = RunMetrics(
            exit_code=0,
            duration_ms=0,
            cloud_call_count=0,
            prompt_tokens_total=0,
            completion_tokens_total=0,
            total_tokens=0,
            context_files_count=0,
            grounded_files_count=0,
            grounded_ratio=None,
            churn_count=0,
            retry_count=0,
            manual_intervention_count=0,
            fallback_used=False,
            quality_score=quality_score,
        )
        return TaskRun(
            task_id=task.id,
            variant=variant,
            command=command,
            output_path=str(output_path),
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            metrics=metrics,
        )

    start = time.monotonic()
    proc = subprocess.run(command, shell=True, text=True, capture_output=True)
    duration_ms = int((time.monotonic() - start) * 1000)
    _ = stdout_path.write_text(proc.stdout)
    _ = stderr_path.write_text(proc.stderr)

    if output_path.exists():
        markdown = output_path.read_text()
    else:
        markdown = proc.stdout
        _ = output_path.write_text(markdown)

    error: str | None = None
    if proc.returncode != 0:
        error = f"command failed with exit code {proc.returncode}"

    metrics = _parse_metrics(
        markdown=markdown,
        duration_ms=duration_ms,
        exit_code=proc.returncode,
        churn_data=churn_data,
        quality_score=quality_score,
    )
    return TaskRun(
        task_id=task.id,
        variant=variant,
        command=command,
        output_path=str(output_path),
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        metrics=metrics,
        error=error,
    )


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = (len(ordered) - 1) * pct
    lower = int(idx)
    upper = min(lower + 1, len(ordered) - 1)
    weight = idx - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _summarize(results: list[TaskRun], variant: str) -> dict[str, float]:
    rows = [r for r in results if r.variant == variant]
    if not rows:
        return {}
    durations = [float(r.metrics.duration_ms) for r in rows]
    cloud_calls = [float(r.metrics.cloud_call_count) for r in rows]
    tokens = [float(r.metrics.total_tokens) for r in rows]
    churn = [float(r.metrics.churn_count) for r in rows]
    retries = [float(r.metrics.retry_count) for r in rows]
    manual = [float(r.metrics.manual_intervention_count) for r in rows]
    fallback_rate = sum(1 for r in rows if r.metrics.fallback_used) / len(rows)
    quality = [
        r.metrics.quality_score for r in rows if r.metrics.quality_score is not None
    ]
    failures = sum(1 for r in rows if r.metrics.exit_code != 0)
    return {
        "runs": float(len(rows)),
        "failures": float(failures),
        "median_churn_count": float(statistics.median(churn)),
        "median_retry_count": float(statistics.median(retries)),
        "median_manual_intervention_count": float(statistics.median(manual)),
        "fallback_rate": float(fallback_rate),
        "median_duration_ms": float(statistics.median(durations)),
        "p90_duration_ms": float(_percentile(durations, 0.9)),
        "median_cloud_calls": float(statistics.median(cloud_calls)),
        "median_total_tokens": float(statistics.median(tokens)),
        "mean_quality_score": float(statistics.mean(quality)) if quality else 0.0,
    }


def _write_report(
    *,
    run_root: Path,
    fixture_path: Path,
    baseline_summary: dict[str, float],
    candidate_summary: dict[str, float],
    mode: str,
) -> None:
    lines: list[str] = []
    lines.append("# Consult Benchmark Report")
    lines.append("")
    lines.append(f"- **Date**: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"- **Fixture**: `{fixture_path}`")
    lines.append(f"- **Mode**: `{mode}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Baseline | Candidate |")
    lines.append("|---|---:|---:|")

    keys = [
        "runs",
        "failures",
        "median_churn_count",
        "median_retry_count",
        "median_manual_intervention_count",
        "fallback_rate",
        "median_cloud_calls",
        "median_total_tokens",
        "median_duration_ms",
        "p90_duration_ms",
        "mean_quality_score",
    ]
    for key in keys:
        b = baseline_summary.get(key, 0.0)
        c = candidate_summary.get(key, 0.0)
        lines.append(f"| {key} | {b:.2f} | {c:.2f} |")

    if baseline_summary and candidate_summary:
        lines.append("")
        lines.append("## Delta (Candidate vs Baseline)")
        lines.append("")
        for key in (
            "median_churn_count",
            "median_cloud_calls",
            "median_total_tokens",
            "median_duration_ms",
        ):
            b = baseline_summary.get(key, 0.0)
            c = candidate_summary.get(key, 0.0)
            if b > 0:
                delta_pct = ((c - b) / b) * 100.0
                lines.append(f"- `{key}`: {delta_pct:+.2f}%")
            else:
                lines.append(f"- `{key}`: n/a (baseline=0)")

    _ = (run_root / "report.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = _build_parser()
    args = _parse_args(parser)

    root = Path.cwd()
    fixture_path = Path(args.fixture)
    if not fixture_path.is_absolute():
        fixture_path = root / fixture_path
    if not fixture_path.exists():
        _ = parser.error(f"Fixture not found: {fixture_path}")

    tasks = _load_tasks(fixture_path)
    if args.task_ids:
        allowed = set(args.task_ids)
        tasks = [t for t in tasks if t.id in allowed]
        if not tasks:
            _ = parser.error("No tasks matched --task-ids")

    ratings = _load_ratings(Path(args.ratings_json)) if args.ratings_json else {}
    churn = _load_churn(Path(args.churn_json)) if args.churn_json else {}

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_root = (
        Path(args.out_dir)
        if args.out_dir
        else root / "tmp" / "consult-benchmark" / timestamp
    )
    run_root.mkdir(parents=True, exist_ok=True)

    variants: list[tuple[str, str]] = []
    if args.mode in {"baseline", "both"}:
        variants.append(("baseline", args.baseline_cmd))
    if args.mode in {"candidate", "both"}:
        variants.append(("candidate", args.candidate_cmd))

    results: list[TaskRun] = []
    for task in tasks:
        for variant, template in variants:
            score = ratings.get(task.id, {}).get(variant)
            churn_data_raw = churn.get(task.id, {}).get(variant, {})
            churn_data = cast(Mapping[str, object], churn_data_raw)
            run = _run_task_variant(
                task=task,
                variant=variant,
                template=template,
                run_root=run_root,
                churn_data=churn_data,
                quality_score=score,
                dry_run=args.dry_run,
            )
            results.append(run)
            status = "DRY" if args.dry_run else f"exit={run.metrics.exit_code}"
            print(f"[{variant}] {task.id}: {status}", file=sys.stderr)

    baseline_summary = _summarize(results, "baseline")
    candidate_summary = _summarize(results, "candidate")
    json_payload: dict[str, object] = {
        "fixture": str(fixture_path),
        "mode": args.mode,
        "dry_run": args.dry_run,
        "churn_input": str(args.churn_json) if args.churn_json else None,
        "results": [asdict(r) for r in results],
        "baseline_summary": baseline_summary,
        "candidate_summary": candidate_summary,
    }
    _ = (run_root / "results.json").write_text(json.dumps(json_payload, indent=2))
    _write_report(
        run_root=run_root,
        fixture_path=fixture_path,
        baseline_summary=baseline_summary,
        candidate_summary=candidate_summary,
        mode=args.mode,
    )

    print(f"Wrote: {run_root / 'results.json'}")
    print(f"Wrote: {run_root / 'report.md'}")


if __name__ == "__main__":
    main()
