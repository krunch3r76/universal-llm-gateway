"""Orchestrate stale-prose scan."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from collections.abc import Callable

from ..dispatch_ops._shared import _FILES_ROOT
from .cross_ref import cross_reference_candidate
from .extractor import extract_candidates
from .models import Finding, FpCounters, ScanReport, SkippedEntry
from .output_writer import write_scan_report
from .target_resolver import resolve_scan_targets
from .verdict import apply_verdict

AssertionsFetch = Callable[[str], list[dict[str, Any]]]
SearchFn = Callable[[str], list[dict[str, Any]]]
AnalyzeImpactFn = Callable[[str, str], float]
OpenFn = Callable[..., object]
FrictionFn = Callable[..., dict[str, Any]]


def scan_targets(
    targets_result: dict[str, object],
    *,
    base: Path,
    principal: str | None,
    fetch_fn: AssertionsFetch,
    search_fn: SearchFn | None = None,
    analyze_impact_fn: AnalyzeImpactFn | None = None,
    open_fn: OpenFn | None = None,
) -> ScanReport:
    targets = targets_result.get("targets") or []
    skipped_raw = targets_result.get("skipped") or []
    skipped = [
        SkippedEntry(**item) if isinstance(item, dict) else item
        for item in skipped_raw
    ]
    findings: list[Finding] = []
    counters = FpCounters()
    opener = open_fn or open

    for target in targets:
        path = target.path if hasattr(target, "path") else target["path"]
        file_path = base / path
        with opener(file_path, encoding="utf-8", errors="replace") as handle:  # type: ignore[call-arg]
            text = handle.read()
        region_start = getattr(target, "region_start", None)
        region_end = getattr(target, "region_end", None)
        if region_start is not None and region_end is not None:
            lines = text.splitlines()
            text = "\n".join(lines[region_start - 1 : region_end])

        candidates = extract_candidates(
            text,
            principal=principal,
            search_fn=search_fn,
        )
        for candidate in candidates:
            hint, row, alignment = cross_reference_candidate(
                candidate,
                fetch_fn=fetch_fn,
                search_fn=search_fn,
                analyze_impact_fn=analyze_impact_fn,
            )
            finding = apply_verdict(
                path=path,
                candidate=candidate,
                full_text=text,
                verdict_hint=hint,
                row=row,
                alignment_score=alignment,
                counters=counters,
            )
            if finding:
                findings.append(finding)

    return ScanReport(
        metadata={
            "scanner": "prose-fact-scanner",
            "timestamp": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            "principal": principal,
        },
        target_count=int(targets_result.get("target_count", 0)),
        excluded_count=int(targets_result.get("excluded_count", 0)),
        findings=findings,
        skipped=skipped,
        fp_counters=counters,
    )


def run_prose_fact_scan(
    *,
    principal: str | None = None,
    paths: list[str] | None = None,
    tier: str | None = None,
    dry_run: bool = False,
    unsafe_full_scan: bool = False,
    files_root: Path | None = None,
    fetch_fn: AssertionsFetch | None = None,
    search_fn: SearchFn | None = None,
    analyze_impact_fn: AnalyzeImpactFn | None = None,
    friction_fn: FrictionFn | None = None,
    open_fn: OpenFn | None = None,
) -> dict[str, Any]:
    del tier  # reserved — all tiers resolved by target_resolver
    base = files_root or _FILES_ROOT
    resolved = resolve_scan_targets(
        base,
        principal=principal,
        paths=paths,
        unsafe_full_scan=unsafe_full_scan,
        open_fn=open_fn,
    )
    if resolved.get("error"):
        return resolved

    if fetch_fn is None:
        return {"error": "fetch_fn is required"}

    report = scan_targets(
        resolved,
        base=base,
        principal=principal,
        fetch_fn=fetch_fn,
        search_fn=search_fn,
        analyze_impact_fn=analyze_impact_fn,
        open_fn=open_fn,
    )
    write_result = write_scan_report(
        report,
        files_root=base,
        dry_run=dry_run,
        friction_fn=friction_fn,
    )
    if write_result.get("error"):
        return write_result
    return {
        **write_result,
        "target_count": report.target_count,
        "excluded_count": report.excluded_count,
        "finding_count": sum(1 for f in report.findings if f.verdict == "STALE"),
        "manifest": resolved.get("manifest"),
    }
