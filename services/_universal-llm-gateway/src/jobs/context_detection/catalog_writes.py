"""Local catalog profile and activated-context writes after measurement completes.

Updates V2 device profiles and metadata in inference_djinn local catalog YAML with
optional fire-and-forget remote sync scheduling when that feature is enabled.
"""

import asyncio
from typing import Any

from universal_logging import get_logger

from .constants import REMOTE_SYNC_ENABLED, REMOTE_SYNC_TIMEOUT_MS

logger = get_logger(__name__)


def update_local_catalog_profile(
    model_id: str,
    context: int,
    vram_mb: int | None,
    ram_mb: int | None,
    n_gpu_layers: int | None,
    device: str | None = None,
) -> bool:
    """
    Update profile in local catalog directly (V2 format).

    Returns True if successful, False otherwise.
    """
    try:
        from inference_djinn.catalog.local_config import (
            load_local_catalog,
            save_local_catalog,
        )

        from ....core.catalog.schemas import SchemaRegistry

        catalog = load_local_catalog()
        models = catalog.get("models", {})

        if model_id not in models:
            logger.warning(f"Model {model_id} not in local catalog")
            return False

        model = models[model_id]

        schema_name = model.get("schema")
        if not schema_name:
            logger.error(
                f"Model '{model_id}' missing required field 'schema' (V2 strict). "
                "Fix the catalog entry and retry measurement."
            )
            return False

        schema = SchemaRegistry.get_by_engine(schema_name)
        if not schema:
            logger.error(f"Unknown schema '{schema_name}' for {model_id}")
            return False

        if device is None:
            if n_gpu_layers is None or n_gpu_layers == 0:
                device = "cpu"
            elif n_gpu_layers == -1:
                device = "gpu"
            else:
                device = "hybrid" if "hybrid" in schema.supported_devices else "gpu"

        if device not in schema.supported_devices:
            logger.warning(
                f"Device '{device}' not supported by schema '{schema_name}', "
                f"skipping profile update for {model_id}"
            )
            return False

        devices = model.setdefault("devices", {})
        device_config = devices.setdefault(device, {})
        profiles = device_config.setdefault("profiles", {})

        profile_entry: dict[str, Any] = {
            "vram_mb": vram_mb or 0,
            "ram_mb": ram_mb or 0,
        }

        if schema.engine == "native" and n_gpu_layers is not None:
            profile_entry["n_gpu_layers"] = n_gpu_layers

        profiles[str(context)] = profile_entry

        save_local_catalog(catalog)
        schedule_remote_sync_nowait(model_id, "profile")
        return True

    except ImportError:
        logger.warning("Local catalog module not available")
        return False
    except Exception as e:
        logger.warning(
            "Failed to update profile for '%s': %s", model_id, e, exc_info=True
        )
        return False


def update_local_catalog_contexts(
    model_id: str, gpu_contexts: list[int], cpu_contexts: list[int]
) -> bool:
    """
    Update activated contexts in local catalog.

    Returns True if successful, False otherwise.
    """
    try:
        from inference_djinn.catalog.local_config import (
            load_local_catalog,
            save_local_catalog,
        )

        catalog = load_local_catalog()
        models = catalog.get("models", {})

        if model_id not in models:
            logger.warning(f"Model {model_id} not in local catalog")
            return False

        model = models[model_id]
        metadata = model.get("metadata", {})

        if gpu_contexts:
            metadata["activated_gpu_contexts"] = gpu_contexts
        if cpu_contexts:
            metadata["activated_cpu_contexts"] = cpu_contexts

        model["metadata"] = metadata
        save_local_catalog(catalog)
        schedule_remote_sync_nowait(model_id, "contexts")
        return True

    except ImportError:
        logger.warning("Local catalog module not available")
        return False
    except Exception as e:
        logger.warning(
            "Failed to update activated contexts for '%s': %s",
            model_id,
            e,
            exc_info=True,
        )
        return False


def schedule_remote_sync_nowait(model_id: str, update_type: str) -> None:
    """
    Schedule optional remote catalog sync (fire-and-forget).

    Non-blocking: creates background task with short timeout.
    Currently disabled (REMOTE_SYNC_ENABLED=False).
    """
    if not REMOTE_SYNC_ENABLED:
        return

    async def _sync_with_timeout() -> None:
        try:
            async with asyncio.timeout(REMOTE_SYNC_TIMEOUT_MS / 1000):
                logger.debug(f"Remote sync stub: {model_id}/{update_type}")
        except TimeoutError:
            logger.warning(f"Remote sync timed out: {model_id}/{update_type}")
        except Exception as e:
            logger.warning(f"Remote sync failed: {model_id}/{update_type}: {e}")

    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_sync_with_timeout())
        task.add_done_callback(lambda t: None)
    except RuntimeError:
        pass
