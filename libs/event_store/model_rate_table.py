"""Write-SOT for model token rates used by dispatch economics dollar equivalents."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .model_rate_cache import clear_rate_caches, load_yaml_payload

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Curated seed (tracked). Override: MODEL_RATES_PATH.
_DEFAULT_RATES_PATH = _REPO_ROOT / "config" / "model_rates.yaml"
# Runtime catalog projection (gitignored host state). Override: MODEL_RATES_CATALOG_PATH.
_CATALOG_PATH = Path.home() / ".gateway" / "model_rates_catalog.yaml"

_CATALOG_ROWS: dict[str, dict[str, Any]] = {}
_CATALOG_GENERATION = 0


@dataclass(frozen=True, slots=True)
class ModelRateRow:
    """USD per million tokens for prompt, optional cache tiers, and completion."""

    model_id: str
    input_rate_per_m: float
    output_rate_per_m: float
    source: str
    updated_at: str
    pinned: bool = False
    cache_write_rate_per_m: float | None = None
    cache_read_rate_per_m: float | None = None


def rates_yaml_path() -> Path:
    """Return the manual override + seed YAML path."""
    override = os.environ.get("MODEL_RATES_PATH", "").strip()
    if override:
        return Path(override)
    return _DEFAULT_RATES_PATH


def catalog_yaml_path() -> Path:
    """Return the persisted catalog-ingest projection path."""
    override = os.environ.get("MODEL_RATES_CATALOG_PATH", "").strip()
    if override:
        return Path(override)
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


_INVALID_OPTIONAL_RATE = object()


def _optional_nonneg_rate(raw: dict[str, Any], key: str) -> float | None | object:
    """Return rate, None if key absent, or a sentinel if present-but-invalid."""
    if key not in raw:
        return None
    value = _coerce_rate(raw.get(key))
    if value is None or value < 0:
        return _INVALID_OPTIONAL_RATE
    return value


def _row_from_mapping(
    model_id: str, raw: dict[str, Any], *, default_source: str
) -> ModelRateRow | None:
    input_rate = _coerce_rate(raw.get("input_rate_per_m"))
    output_rate = _coerce_rate(raw.get("output_rate_per_m"))
    if input_rate is None or output_rate is None:
        return None
    if input_rate < 0 or output_rate < 0:
        return None
    cache_write = _optional_nonneg_rate(raw, "cache_write_rate_per_m")
    cache_read = _optional_nonneg_rate(raw, "cache_read_rate_per_m")
    if cache_write is _INVALID_OPTIONAL_RATE or cache_read is _INVALID_OPTIONAL_RATE:
        return None
    return ModelRateRow(
        model_id=model_id,
        input_rate_per_m=input_rate,
        output_rate_per_m=output_rate,
        source=str(raw.get("source") or default_source),
        updated_at=str(raw.get("updated_at") or _now_iso()),
        pinned=bool(raw.get("pinned")),
        cache_write_rate_per_m=cache_write if isinstance(cache_write, float) else None,
        cache_read_rate_per_m=cache_read if isinstance(cache_read, float) else None,
    )


def _load_rates_payload(path: Path | None = None) -> dict[str, Any]:
    """Parse the manual rates YAML; empty dict on missing or invalid file."""
    yaml_path = path or rates_yaml_path()
    if not yaml_path.is_file():
        return {}
    try:
        return load_yaml_payload(yaml_path)
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Failed to load model rates YAML %s: %s", yaml_path, exc)
        return {}


def _normalize_knob_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip().lower()


def _normalize_knobs(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key or "").strip()
        if not name or value is None:
            continue
        out[name] = _normalize_knob_value(value)
    return out


@dataclass(frozen=True, slots=True)
class KnobVariantRate:
    """Pinned rates for a (model_id, knobs-subset) join — not a fake model_id."""

    model_id: str
    knobs: tuple[tuple[str, str], ...]
    row: ModelRateRow

    def matches(self, requested: dict[str, str]) -> bool:
        return all(requested.get(key) == value for key, value in self.knobs)


def load_knob_variants(path: Path | None = None) -> list[KnobVariantRate]:
    """Load knob-priced variants from the same YAML as manual model rows."""
    payload = _load_rates_payload(path)
    entries = payload.get("knob_variants") or []
    if not isinstance(entries, list):
        return []
    variants: list[KnobVariantRate] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("model_id") or "").strip()
        knobs = _normalize_knobs(entry.get("knobs"))
        if not model_id or not knobs:
            continue
        row = _row_from_mapping(model_id, entry, default_source="manual_seed")
        if row is None:
            continue
        variants.append(
            KnobVariantRate(
                model_id=model_id,
                knobs=tuple(sorted(knobs.items())),
                row=row,
            )
        )
    return variants


def load_manual_rows(
    path: Path | None = None,
) -> tuple[dict[str, ModelRateRow], dict[str, str]]:
    """Load seed/override rows and alias map from YAML."""
    payload = _load_rates_payload(path)
    if not payload:
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


def _pricing_tuple(raw: dict[str, Any]) -> tuple[Any, ...]:
    """Comparable pricing fields — excludes updated_at (refresh noise)."""
    return (
        _coerce_rate(raw.get("input_rate_per_m")),
        _coerce_rate(raw.get("output_rate_per_m")),
        str(raw.get("source") or ""),
        bool(raw.get("pinned")),
        _coerce_rate(raw.get("cache_write_rate_per_m")),
        _coerce_rate(raw.get("cache_read_rate_per_m")),
    )


def _load_catalog_raw_from_disk(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load raw catalog dicts from disk for pricing-equality checks."""
    yaml_path = path or catalog_yaml_path()
    if not yaml_path.is_file():
        return {}
    try:
        payload = load_yaml_payload(yaml_path)
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Failed to load catalog rates YAML %s: %s", yaml_path, exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    rows: dict[str, dict[str, Any]] = {}
    models = payload.get("models") or []
    if not isinstance(models, list):
        return {}
    for entry in models:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("model_id") or "").strip()
        if model_id:
            rows[model_id] = dict(entry)
    return rows


def load_catalog_rows_from_disk(path: Path | None = None) -> dict[str, ModelRateRow]:
    """Load catalog-ingest projection rows written by cloud-proxy refresh."""
    rows: dict[str, ModelRateRow] = {}
    for model_id, entry in _load_catalog_raw_from_disk(path).items():
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
    _invalidate_rate_cache()


def upsert_catalog_models(
    models: list[Any],
    *,
    source: str = "catalog_refresh",
) -> dict[str, int]:
    """Upsert catalog pricing rows; pinned manual rows are not overwritten.

    Disk write is skipped when no pricing fields changed (timestamp-only
    refreshes must not rewrite the host catalog projection).
    """
    manual_rows, _ = load_manual_rows()
    prior: dict[str, dict[str, Any]] = _load_catalog_raw_from_disk()
    prior.update(_CATALOG_ROWS)
    upserted = 0
    pricing_changed = 0
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
        for write_id in ids_to_write:
            candidate = {
                "model_id": write_id,
                "input_rate_per_m": input_rate,
                "output_rate_per_m": output_rate,
                "source": source,
                "pinned": False,
            }
            existing = prior.get(write_id)
            if existing is not None and _pricing_tuple(existing) == _pricing_tuple(
                candidate
            ):
                candidate["updated_at"] = str(existing.get("updated_at") or _now_iso())
            else:
                candidate["updated_at"] = _now_iso()
                pricing_changed += 1
            _CATALOG_ROWS[write_id] = candidate
            prior[write_id] = candidate
            upserted += 1

    if pricing_changed:
        save_catalog_rows_to_disk(_CATALOG_ROWS)
        _invalidate_rate_cache()

    counts = {
        "upserted": upserted,
        "pricing_changed": pricing_changed,
        "skipped": skipped,
        "rejected_negative": rejected_negative,
        "rejected_zero_rate": rejected_zero_rate,
    }
    logger.info("Model rate table catalog ingest: %s", counts)
    return counts


def clear_catalog_rows_for_tests() -> None:
    """Reset in-memory catalog projection (tests only)."""
    global _CATALOG_GENERATION
    _CATALOG_ROWS.clear()
    _CATALOG_GENERATION += 1
    _invalidate_rate_cache()


def _invalidate_rate_cache() -> None:
    """Invalidate merged rate projections after a catalog state change."""
    clear_rate_caches()
    _merged_rate_table.cache_clear()


def _path_cache_key(path: Path) -> tuple[str, int, int] | None:
    """Return a cache key that changes when a YAML source changes."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=32)
def _merged_rate_table(
    rates_key: tuple[str, int, int] | None,
    catalog_key: tuple[str, int, int] | None,
    generation: int,
) -> tuple[dict[str, ModelRateRow], dict[str, str]]:
    """Build the merged rate and alias maps once per source-file version."""
    manual_rows, aliases = load_manual_rows(Path(rates_key[0]) if rates_key else None)
    merged = load_catalog_rows_from_disk(Path(catalog_key[0]) if catalog_key else None)
    for catalog_id, raw in _CATALOG_ROWS.items():
        row = _row_from_mapping(catalog_id, raw, default_source="catalog_refresh")
        if row is not None:
            merged[catalog_id] = row
    for manual_id, manual_row in manual_rows.items():
        existing = merged.get(manual_id)
        if existing is None or manual_row.pinned:
            merged[manual_id] = manual_row
    return merged, aliases


def resolve_rate(
    model_id: str | None,
    resolved_model: str | None = None,
    *,
    knobs: dict[str, Any] | None = None,
    path: Path | None = None,
) -> ModelRateRow | None:
    """Lookup rate by (model_id, knobs) variant, then model_id / alias / resolved_model."""
    global _CATALOG_GENERATION
    rates_path = path or rates_yaml_path()
    catalog_path = catalog_yaml_path()
    merged, aliases = _merged_rate_table(
        _path_cache_key(rates_path),
        _path_cache_key(catalog_path),
        _CATALOG_GENERATION,
    )
    keys: list[str] = []
    for candidate in (model_id, resolved_model):
        if candidate and candidate not in keys:
            keys.append(candidate)
    for candidate in list(keys):
        alias_target = aliases.get(candidate)
        if alias_target and alias_target not in keys:
            keys.append(alias_target)

    requested = _normalize_knobs(knobs)
    if requested and keys:
        matches = [
            variant
            for variant in load_knob_variants(path)
            if variant.model_id in keys and variant.matches(requested)
        ]
        if matches:
            return max(matches, key=lambda variant: len(variant.knobs)).row

    for key in keys:
        row = merged.get(key)
        if row is not None:
            return row
    return None
