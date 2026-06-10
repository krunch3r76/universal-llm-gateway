"""Render FactoryRecords to markdown tables + JSON sidecar."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from .extract import FactoryRecord

_HEADER = "| Signal | Required Payload | Optional Payload |\n|--------|------------------|------------------|"


def _required_cell(rec: FactoryRecord) -> str:
    parts = [f"`{k}`" for k in rec.required_keys] + [
        f"`{k}?`" for k in rec.optional_keys
    ]
    if rec.payload_dynamic:
        parts.append("_dynamic_")
    return ", ".join(parts) if parts else "-"


def _optional_cell(rec: FactoryRecord, overlay: dict[str, str]) -> str:
    return overlay.get(rec.signal) or rec.description or "-"


def render_domain_table(
    domain: str, records: list[FactoryRecord], overlay: dict[str, str]
) -> str:
    rows = [
        f"| `{r.signal}` | {_required_cell(r)} | {_optional_cell(r, overlay)} |"
        for r in sorted(records, key=lambda r: r.signal)
    ]
    return f"{_HEADER}\n" + "\n".join(rows)


def render_region(
    domain: str,
    records: list[FactoryRecord],
    overlay: dict[str, str],
    inventory_sha: str,
) -> str:
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = render_domain_table(domain, records, overlay)
    return (
        f"<!-- GENERATED:START region={domain} inventory_sha={inventory_sha} generated={ts} -->\n"
        f"{body}\n"
        f"<!-- GENERATED:END region={domain} -->"
    )


def render_json_sidecar(records: list[FactoryRecord]) -> str:
    payload = {
        "records": [r.__dict__ for r in sorted(records, key=lambda r: r.signal)],
        "dynamic_unresolved": [
            {"factory": r.factory_name, "source": f"{r.source_path}:{r.lineno}"}
            for r in records
            if r.signal_dynamic
        ],
    }
    return json.dumps(payload, indent=2, default=list)
