"""Pure helpers for the live_verify verdict step."""

from __future__ import annotations

import json
from typing import Any

# Per-line probe_call / observed blobs (caller-supplied), UTF-8 JSON size cap.
_LINE_DATA_MAX_BYTES = 8192


def _cap_json_dict(value: dict[str, Any], *, max_bytes: int = _LINE_DATA_MAX_BYTES) -> dict[str, Any]:
    """Store dict as data; if serialized size exceeds cap, mark truncation."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return {
        "_size_truncated": True,
        "_original_bytes": len(encoded),
        "_max_bytes": max_bytes,
    }


def _line_probe_call(item: dict[str, Any], default: dict[str, Any]) -> dict[str, Any]:
    supplied = item.get("probe_call")
    if isinstance(supplied, dict):
        return _cap_json_dict(supplied)
    return default


def _line_observed(item: dict[str, Any], default: dict[str, Any]) -> dict[str, Any]:
    supplied = item.get("observed")
    if isinstance(supplied, dict):
        return _cap_json_dict(supplied)
    return default


def acceptance_lines_from_description(description: str) -> list[str]:
    """Lines shaped ``(a) ...`` in a todo description."""
    lines: list[str] = []
    for raw in description.splitlines():
        text = raw.strip()
        if len(text) >= 4 and text[0] == "(" and text[2] == ")" and text[1].islower():
            lines.append(text)
    return lines


def relations_from_snapshot(
    snapshot: dict[str, Any],
    services: list[str],
) -> list[dict[str, Any]]:
    """Per-service relation stored on the verdict row.

    Unreachable probe and ``liveness.answer != yes`` stay on the row.
    Close folds those into ``live_verify.land_sha_not_live``.
    """
    if "error" in snapshot or not isinstance(snapshot.get("services"), list):
        reason = snapshot.get("error")
        if isinstance(reason, str) and reason.strip():
            absent = reason.strip()
        else:
            absent = "probe returned no code_ref relation"
        return [
            {
                "service": service,
                "relation": None,
                "answer": "unknown",
                "relation_absent_reason": absent,
                "close_gate": "live_verify.land_sha_not_live",
            }
            for service in services
        ]
    by_service = {
        row.get("service"): row for row in snapshot["services"] if isinstance(row, dict)
    }
    relations: list[dict[str, Any]] = []
    for service in services:
        row = by_service.get(service)
        liveness = {}
        if isinstance(row, dict):
            validation = row.get("code_ref_validation")
            if isinstance(validation, dict):
                raw_live = validation.get("liveness")
                if isinstance(raw_live, dict):
                    liveness = raw_live
        answer = liveness.get("answer")
        if answer not in {"yes", "no", "unknown"}:
            answer = "unknown"
        relation = liveness.get("relation")
        entry: dict[str, Any] = {
            "service": service,
            "relation": relation,
            "answer": answer,
        }
        if relation is None:
            observation = liveness.get("observation")
            probe_error = (
                observation.get("probe_error")
                if isinstance(observation, dict)
                else None
            )
            reason = liveness.get("reason")
            if isinstance(reason, str) and reason.strip():
                absent = reason.strip()
                if isinstance(probe_error, str) and probe_error.strip():
                    entry["probe_error"] = probe_error.strip()
            elif isinstance(probe_error, str) and probe_error.strip():
                absent = probe_error.strip()
            else:
                absent = "probe returned no code_ref relation"
            entry["relation_absent_reason"] = absent
            entry["close_gate"] = "live_verify.land_sha_not_live"
        relations.append(entry)
    return relations


def normalize_lines(
    raw_lines: list[Any],
    *,
    probe_call: dict[str, Any],
    observed: dict[str, Any],
) -> list[dict[str, Any]]:
    """One recorded row per acceptance line. Default verdict is untestable."""
    rows: list[dict[str, Any]] = []
    for item in raw_lines:
        line_probe = probe_call
        line_observed = observed
        if isinstance(item, str):
            text = item
            verdict = "LIVE_UNTESTABLE"
        elif isinstance(item, dict) and isinstance(item.get("line"), str):
            text = item["line"]
            supplied = item.get("verdict")
            verdict = (
                supplied
                if supplied in {"LIVE_OK", "LIVE_DEFECT", "LIVE_UNTESTABLE"}
                else "LIVE_UNTESTABLE"
            )
            line_probe = _line_probe_call(item, probe_call)
            line_observed = _line_observed(item, observed)
        else:
            continue
        rows.append(
            {
                "line": text,
                "probe_call": line_probe,
                "observed": line_observed,
                "verdict": verdict,
            }
        )
    return rows


def verdict_claim(lines: list[dict[str, Any]]) -> str:
    """Claim text cites each line, probe, observation, and verdict."""
    parts = [
        (
            f"line={row['line']}; probe={json.dumps(row['probe_call'], sort_keys=True)}; "
            f"observed={json.dumps(row['observed'], sort_keys=True)}; "
            f"verdict={row['verdict']}"
        )
        for row in lines
    ]
    return "live_verify " + " | ".join(parts)


def verdict_attributes(
    *,
    land_sha: str,
    services: list[str],
    lines: list[dict[str, Any]],
    service_relations: list[dict[str, Any]],
    operator_ruling: str | None = None,
) -> dict[str, Any]:
    """Full attribute set for assert or supersede. Ruling omitted unless set."""
    attrs: dict[str, Any] = {
        "kind": "live_verify",
        "land_sha": land_sha,
        "services": list(services),
        "lines": lines,
        "service_relations": service_relations,
    }
    if operator_ruling is not None:
        attrs["operator_ruling"] = operator_ruling
    return attrs


def ruling_attributes(prior: dict[str, Any], ruling: str) -> dict[str, Any]:
    """Supersede attribute set: same verdict facts plus operator_ruling."""
    return verdict_attributes(
        land_sha=str(prior.get("land_sha") or ""),
        services=list(prior.get("services") or []),
        lines=list(prior.get("lines") or []),
        service_relations=list(prior.get("service_relations") or []),
        operator_ruling=ruling,
    )
