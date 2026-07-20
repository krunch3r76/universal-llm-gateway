"""Write-SOT for model token rates used by dispatch economics dollar equivalents."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_RATES_PATH = _REPO_ROOT / "data" / "model_rates.yaml"
_CATALOG_PATH = _REPO_ROOT / "data" / "model_rates_catalog.yaml"

_CATALOG_ROWS: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True, slots=True)
class ModelRateRow:
    """USD per million tokens for prompt and completion."""

    model_id: str
    input_rate_per_m: float
    output_rate_per_m: float
    source: str
    updated_at: str
    pinned: bool = False


def rates_yaml_path() -> Path:
    """Return the manual override + seed YAML path."""
    return _DEFAULT_RATES_PATH


def catalog_yaml_path() -> Path:
    """Return the persisted catalog-ingest projection path."""
    return _CATALOG_PATH


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _coerce_rate(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _row_from_mapping(model_id: str, raw: dict[str, Any], *, default_source: str) -> ModelRateRow | None:
    input_rate = _coerce_rate(raw.get("input_rate_per_m"))
    output_rate = _coerce_rate(raw.get("output_rate_per_m"))
    if input_rate is None or output_rate is None:
        return None
    if input_rate < 0 or output_rate < 0:
        return None
    return ModelRateRow(
        model_id=model_id,
        input_rate_per_m=input_rate,
        output_rate_per_m=output_rate,
        source=str(raw.get("source") or default_source),
        updated_at=str(raw.get("updated_at") or _now_iso()),
        pinned=bool(raw.get("pinned")),
    )


def load_manual_rows(path: Path | None = None) -> tuple[dict[str, ModelRateRow], dict[str, str]]:
    """Load seed/override rows and alias map from YAML."""
    yaml_path = path or rates_yaml_path()
    if not yaml_path.is_file():
        return {}, {}
    try:
        payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Failed to load model rates YAML %s: %s", yaml_path, exc)
        return {}, {}
    if not isinstance(payload, dict):
        return {}, {}

    aliases_raw = payload.get("aliases") or {}
    aliases = {
        str(alias): str(target)
        for alias, target in aliases_raw.items()
        if alias and target
    }

    rows: dict[str, ModelRateRow] = {}
    models = payload.get("models") or []
    if isinstance(models, list):
        for entry in models:
            if not isinstance(entry, dict):
                continue
            model_id = str(entry.get("model_id") or "").strip()
            if not model_id:
                continue
            row = _row_from_mapping(model_id, entry, default_source="manual_seed")
            if row is not None:
                rows[model_id] = row
    return rows, aliases


def load_catalog_rows_from_disk(path: Path | None = None) -> dict[str, ModelRateRow]:
    """Load catalog-ingest projection rows written by cloud-proxy refresh."""
    yaml_path = path or catalog_yaml_path()
    if not yaml_path.is_file():
        return {}
    try:
        payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Failed to load catalog rates YAML %s: %s", yaml_path, exc)
        return {}
    if not isinstance(payload, dict):
        return {}

    rows: dict[str, ModelRateRow] = {}
    models = payload.get("models") or []
    if isinstance(models, list):
        for entry in models:
            if not isinstance(entry, dict):
                continue
            model_id = str(entry.get("model_id") or "").strip()
            if not model_id:
                continue
            row = _row_from_mapping(model_id, entry, default_source="catalog_refresh")
            if row is not None:
                rows[model_id] = row
    return rows


def save_catalog_rows_to_disk(
    rows: dict[str, dict[str, Any]],
    path: Path | None = None,
) -> None:
    """Persist in-memory catalog projection for cross-process ES pricing lookup."""
    yaml_path = path or catalog_yaml_path()
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": _now_iso(),
        "models": sorted(rows.values(), key=lambda raw: str(raw.get("model_id") or "")),
    }
    yaml_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def upsert_catalog_models(
    models: list[Any],
    *,
    source: str = "catalog_refresh",
) -> dict[str, int]:
    """Upsert catalog pricing rows; pinned manual rows are not overwritten."""
    manual_rows, _ = load_manual_rows()
    upserted = 0
    skipped = 0
    rejected_negative = 0
    rejected_zero_rate = 0

    for model in models:
        model_id = str(getattr(model, "id", "") or "").strip()
        if not model_id:
            skipped += 1
            continue
        manual = manual_rows.get(model_id)
        if manual is not None and manual.pinned:
            skipped += 1
            continue
        input_rate = _coerce_rate(getattr(model, "prompt_cost_per_m", None))
        output_rate = _coerce_rate(getattr(model, "completion_cost_per_m", None))
        if input_rate is None or output_rate is None:
            skipped += 1
            continue
        if input_rate < 0 or output_rate < 0:
            rejected_negative += 1
            continue
        if input_rate == 0.0 and output_rate == 0.0:
            rejected_zero_rate += 1
            continue
        ids_to_write = [model_id]
        # OpenRouter rows are openrouter/<provider>/<model>; rollups use bare
        # provider-canonical ids — dual-key so resolve_rate hits real prices.
        if model_id.startswith("openrouter/"):
            bare_id = model_id.removeprefix("openrouter/")
            if "/" in bare_id and bare_id not in ids_to_write:
                bare_manual = manual_rows.get(bare_id)
                if bare_manual is None or not bare_manual.pinned:
                    ids_to_write.append(bare_id)
        updated_at = _now_iso()
        for write_id in ids_to_write:
            _CATALOG_ROWS[write_id] = {
                "model_id": write_id,
                "input_rate_per_m": input_rate,
                "output_rate_per_m": output_rate,
                "source": source,
                "updated_at": updated_at,
                "pinned": False,
            }
            upserted += 1

    if upserted:
        save_catalog_rows_to_disk(_CATALOG_ROWS)

    counts = {
        "upserted": upserted,
        "skipped": skipped,
        "rejected_negative": rejected_negative,
        "rejected_zero_rate": rejected_zero_rate,
    }
    logger.info("Model rate table catalog ingest: %s", counts)
    return counts


def clear_catalog_rows_for_tests() -> None:
    """Reset in-memory catalog projection (tests only)."""
    _CATALOG_ROWS.clear()


def resolve_rate(
    model_id: str | None,
    resolved_model: str | None = None,
    *,
    path: Path | None = None,
) -> ModelRateRow | None:
    """Lookup rate by model_id, alias map, then resolved_model key."""
    manual_rows, aliases = load_manual_rows(path)
    keys: list[str] = []
    for candidate in (model_id, resolved_model):
        if candidate and candidate not in keys:
            keys.append(candidate)
    for candidate in list(keys):
        alias_target = aliases.get(candidate)
        if alias_target and alias_target not in keys:
            keys.append(alias_target)

    merged: dict[str, ModelRateRow] = {}
    for catalog_id, row in load_catalog_rows_from_disk().items():
        merged[catalog_id] = row
    for catalog_id, raw in _CATALOG_ROWS.items():
        row = _row_from_mapping(catalog_id, raw, default_source="catalog_refresh")
        if row is not None:
            merged[catalog_id] = row
    # Only pinned manual rows override catalog; non-pinned seeds fill gaps only.
    for manual_id, manual_row in manual_rows.items():
        existing = merged.get(manual_id)
        if existing is None or manual_row.pinned:
            merged[manual_id] = manual_row

    for key in keys:
        row = merged.get(key)
        if row is not None:
            return row
    return None
