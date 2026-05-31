"""Local catalog VRAM reconciliation for measured model loads."""

from __future__ import annotations

import copy
from typing import Any

from universal_logging import get_logger

from src.core.catalog import get_catalog_loader
from src.core.catalog_manager import get_catalog_manager
from src.core.model_registry.registry import normalize_model_id
from src.core.synthetic_models import SyntheticModelResolver

logger = get_logger(__name__)


def _resolve_target_profile(
    entry: dict[str, Any],
    canonical_model_id: str,
    measured_vram_mb: int,
) -> tuple[str, str] | None:
    """Resolve the device/profile entry to update in the local catalog."""
    synthetic = SyntheticModelResolver.resolve_synthetic_id(canonical_model_id)
    if synthetic:
        _, context_length, is_cpu, is_hybrid = synthetic
        device = "cpu" if is_cpu else "hybrid" if is_hybrid else "gpu"
        return device, str(context_length)

    devices = entry.get("devices") or {}
    preferred_devices = ["gpu", "hybrid", "cpu"] if measured_vram_mb > 0 else ["cpu"]

    for device in preferred_devices:
        profiles = (devices.get(device) or {}).get("profiles") or {}
        if profiles:
            profile_key = "default" if "default" in profiles else next(iter(profiles))
            return device, str(profile_key)

    return None


def reconcile_max_observed_vram(
    model_id: str,
    measured_vram_mb: int,
    measured_ram_mb: int,
) -> bool:
    """
    Persist a higher measured VRAM value into the local operational catalog.

    Invariant: catalog vram_mb is monotonic non-decreasing under runtime
    reconciliation. Lower observations never overwrite a higher prior value.
    """
    if measured_vram_mb <= 0:
        return False

    canonical_model_id = normalize_model_id(model_id)
    synthetic = SyntheticModelResolver.resolve_synthetic_id(canonical_model_id)
    base_model_id = synthetic[0] if synthetic else canonical_model_id

    entry = get_catalog_loader().get_model(base_model_id)
    if not entry:
        logger.warning(
            "Skipping VRAM reconciliation for %s: model missing from merged catalog",
            model_id,
        )
        return False

    entry = copy.deepcopy(entry)
    target = _resolve_target_profile(entry, canonical_model_id, measured_vram_mb)
    if target is None:
        logger.warning(
            "Skipping VRAM reconciliation for %s: no target profile resolved",
            model_id,
        )
        return False

    device_key, profile_key = target
    devices = entry.get("devices") or {}
    profiles = (devices.get(device_key) or {}).get("profiles") or {}
    profile = profiles.get(profile_key)
    if not isinstance(profile, dict):
        logger.warning(
            "Skipping VRAM reconciliation for %s: profile %s/%s missing in local entry",
            model_id,
            device_key,
            profile_key,
        )
        return False

    current_vram_mb = int(profile.get("vram_mb") or 0)
    if measured_vram_mb <= current_vram_mb:
        logger.info(
            "Skipping VRAM reconciliation for %s: measured=%sMB <= current=%sMB",
            model_id,
            measured_vram_mb,
            current_vram_mb,
        )
        return False

    profile["vram_mb"] = int(measured_vram_mb)
    if "ram_mb" not in profile:
        profile["ram_mb"] = int(measured_ram_mb or 0)

    get_catalog_manager().upsert_local_only(base_model_id, entry)
    logger.info(
        "Reconciled local catalog VRAM for %s via %s/%s: %sMB -> %sMB",
        model_id,
        device_key,
        profile_key,
        current_vram_mb,
        measured_vram_mb,
    )
    return True
