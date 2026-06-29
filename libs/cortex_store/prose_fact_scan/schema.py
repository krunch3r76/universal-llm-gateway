"""Scan report schema validation."""

from __future__ import annotations

from typing import Any


def validate_report_json(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_top = (
        "metadata",
        "target_count",
        "excluded_count",
        "findings",
        "skipped",
        "friction_ids",
        "fp_counters",
    )
    for key in required_top:
        if key not in report:
            errors.append(f"missing key: {key}")

    findings = report.get("findings")
    if isinstance(findings, list):
        for idx, item in enumerate(findings):
            for field in (
                "verdict",
                "path",
                "predicate_form",
                "line_start",
                "line_end",
            ):
                if field not in item:
                    errors.append(f"findings[{idx}] missing {field}")
    else:
        errors.append("findings must be a list")

    skipped = report.get("skipped")
    if isinstance(skipped, list):
        for idx, item in enumerate(skipped):
            if "reason" not in item:
                errors.append(f"skipped[{idx}] missing reason")
    else:
        errors.append("skipped must be a list")

    counters = report.get("fp_counters")
    if not isinstance(counters, dict):
        errors.append("fp_counters must be a dict")
    return errors
