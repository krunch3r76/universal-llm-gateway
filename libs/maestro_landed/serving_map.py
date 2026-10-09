"""Load path-prefix → manage service rows from config/maestro/serving-map.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ServingRow:
    prefix: str
    services: tuple[str, ...]
    cite: str


@dataclass(frozen=True)
class ServingMap:
    version: int
    source_pins: dict[str, dict[str, str]]
    rows: tuple[ServingRow, ...]

    def services_for_path(self, path: str) -> tuple[str, ...] | None:
        normalized = path.replace("\\", "/")
        best: ServingRow | None = None
        best_len = -1
        for row in self.rows:
            if normalized.startswith(row.prefix) and len(row.prefix) > best_len:
                best = row
                best_len = len(row.prefix)
        return tuple(best.services) if best else None


def load_serving_map(path: Path | None = None) -> ServingMap:
    repo_root = Path(__file__).resolve().parents[2]
    yaml_path = path or repo_root / "config" / "maestro" / "serving-map.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("serving-map root must be a mapping")
    rows_raw = data.get("rows") or []
    rows: list[ServingRow] = []
    for item in rows_raw:
        if not isinstance(item, dict):
            continue
        prefix = str(item.get("prefix") or "")
        cite = str(item.get("cite") or "").strip()
        if not prefix or not cite:
            raise ValueError("each row requires non-empty prefix and cite")
        services = item.get("services") or []
        if not isinstance(services, list) or not services:
            raise ValueError(f"row {prefix!r} requires non-empty services list")
        rows.append(
            ServingRow(
                prefix=prefix,
                services=tuple(str(s) for s in services),
                cite=cite,
            )
        )
    pins = data.get("source_pins")
    if not isinstance(pins, dict):
        raise ValueError("source_pins must be a mapping")
    version = int(data.get("version") or 0)
    return ServingMap(version=version, source_pins=pins, rows=tuple(rows))


def propagation_service_diff(
    restart_services: set[str],
    propagation: list[Any] | None,
) -> list[str]:
    """Services named in propagation but not derived from touched paths."""
    if not propagation:
        return []
    prop_services: set[str] = set()
    for item in propagation:
        if isinstance(item, dict):
            svc = item.get("service")
            if isinstance(svc, str) and svc.strip():
                prop_services.add(svc.strip())
    return sorted(prop_services - restart_services)
