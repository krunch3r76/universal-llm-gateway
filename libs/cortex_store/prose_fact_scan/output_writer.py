"""JSON report writer + friction emission."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from durable_io.atomic import durable_write_text

from ..dispatch_ops._shared import _FILES_ROOT
from .constants import REPORT_DIR, SERVICE_ENTITY_ID, SERVICE_OWNER
from .models import Finding, ScanReport
from .schema import validate_report_json

FrictionFn = Callable[..., dict[str, Any]]


def _finding_dict(finding: Finding) -> dict[str, Any]:
    return {
        "verdict": finding.verdict,
        "path": finding.path,
        "entity_id": finding.entity_id,
        "predicate_form": finding.predicate_form,
        "line_start": finding.line_start,
        "line_end": finding.line_end,
        "clause": finding.clause,
        "assertion_id": finding.assertion_id,
        "severity": finding.severity,
    }


def build_report_dict(report: ScanReport) -> dict[str, Any]:
    return {
        "metadata": report.metadata,
        "target_count": report.target_count,
        "excluded_count": report.excluded_count,
        "findings": [_finding_dict(f) for f in report.findings],
        "skipped": [s.__dict__ for s in report.skipped],
        "friction_ids": report.friction_ids,
        "fp_counters": report.fp_counters.to_dict(),
    }


def write_scan_report(
    report: ScanReport,
    *,
    files_root: Path | None = None,
    dry_run: bool = False,
    friction_fn: FrictionFn | None = None,
) -> dict[str, Any]:
    root = files_root or _FILES_ROOT
    ts = report.metadata.get("timestamp") or datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    out_dir = root / REPORT_DIR / ts
    payload = build_report_dict(report)
    errors = validate_report_json(payload)
    if errors:
        return {"error": "invalid report schema", "details": errors}

    friction_ids: list[int] = []
    if not dry_run and friction_fn:
        stale_pairs: dict[tuple[str, str], Finding] = {}
        for finding in report.findings:
            if finding.verdict != "STALE" or not finding.entity_id:
                continue
            key = (finding.path, finding.entity_id)
            if key not in stale_pairs:
                stale_pairs[key] = finding
        for (path, entity_id), finding in stale_pairs.items():
            note = (
                f"Stale prose in {path}:{finding.line_start}-{finding.line_end} "
                f"for {entity_id} contradicts assertion {finding.assertion_id} "
                f"({finding.predicate_form})"
            )
            result = friction_fn(
                owner=SERVICE_OWNER,
                category="stale_context",
                note=note,
                agent="prose-fact-scanner",
            )
            if result.get("error"):
                return result
            item = result.get("item") or result.get("assertion") or {}
            friction_id = item.get("id")
            if friction_id is not None:
                friction_ids.append(int(friction_id))

    report.friction_ids = friction_ids
    payload["friction_ids"] = friction_ids

    if dry_run:
        return {"report": payload, "report_dir": str(out_dir), "dry_run": True}

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    durable_write_text(
        json_path, json.dumps(payload, indent=2), retain_store_root=root
    )
    return {
        "report": payload,
        "report_dir": str(out_dir.relative_to(root)),
        "report_path": str(json_path.relative_to(root)),
        "friction_ids": friction_ids,
        "service_entity_id": SERVICE_ENTITY_ID,
    }
