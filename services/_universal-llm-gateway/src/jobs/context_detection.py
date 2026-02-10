"""
Context detection and activation logic for measurement jobs.

Provides smart context detection from model metadata and
determines optimal activated contexts based on measurement results.

Catalog Updates:
- Primary path: Local catalog writes (synchronous, always enabled)
- Optional: Remote sync hook (background task, disabled by default)
"""

import asyncio
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

# Remote sync configuration (disabled by default)
# TODO: Enable when remote catalog sync is implemented
REMOTE_SYNC_ENABLED = False
REMOTE_SYNC_TIMEOUT_MS = 500  # Short timeout to avoid blocking

# Standard context sizes for step-down (descending order)
STANDARD_CONTEXTS = [131072, 65536, 32768, 16384, 8192, 4096, 2048, 1024]


@contextmanager
def catalog_write_lock(catalog_path: Path):
    """
    Acquire exclusive write lock on catalog file.

    Prevents concurrent measurements from corrupting the catalog.
    Uses fcntl (Unix) for file-level locking.
    """
    lock_file = catalog_path.parent / f".{catalog_path.name}.lock"
    lock_file.touch(exist_ok=True)

    with open(lock_file, "w") as lock_fd:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)  # Exclusive lock
            yield
        finally:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)  # Release lock


def get_step_down_contexts(training_ctx: int) -> list[int]:
    """
    Get context sizes to try, starting from training_context_length.

    Returns contexts in descending order, starting from the largest
    standard context <= training_ctx.
    """
    contexts = [c for c in STANDARD_CONTEXTS if c <= training_ctx]
    # Include training_ctx itself if it's not a standard size
    if training_ctx not in contexts and training_ctx > 0:
        contexts = [training_ctx] + contexts
    return sorted(contexts, reverse=True)


def extract_training_context_from_gguf(file_path: Path) -> int | None:
    """
    Extract training_context_length directly from GGUF file metadata.

    Args:
        file_path: Path to GGUF model file

    Returns:
        Training context length or None if extraction fails
    """
    try:
        import gguf

        reader = gguf.GGUFReader(str(file_path))
        fields = reader.fields

        # First, try to get architecture-specific field
        arch_field = fields.get("general.architecture")
        if arch_field and arch_field.data:
            arch_value = arch_field.parts[arch_field.data[0]]
            if hasattr(arch_value, "tobytes"):
                arch = arch_value.tobytes().decode("utf-8").rstrip("\x00").strip()
            elif isinstance(arch_value, str):
                arch = arch_value
            else:
                arch = str(arch_value)

            # Try architecture-specific context field first
            arch_context_field = f"{arch}.context_length"
            if arch_context_field in fields:
                field = fields[arch_context_field]
                if field.data:
                    value = field.parts[field.data[0]]
                    if isinstance(value, list | tuple) and len(value) > 0:
                        return int(value[0])
                    if hasattr(value, "item"):
                        return int(value.item())
                    return int(value)

        # Fall back to common context length field names
        context_field_names = [
            "llama.context_length",
            "context_length",
            "n_ctx_train",
            "max_position_embeddings",
        ]

        for field_name in context_field_names:
            if field_name in fields:
                field = fields[field_name]
                if field.data:
                    value = field.parts[field.data[0]]
                    if isinstance(value, list | tuple) and len(value) > 0:
                        return int(value[0])
                    # Handle numpy arrays and scalars
                    if hasattr(value, "item"):  # numpy array/scalar
                        return int(value.item())
                    return int(value)

        logger.warning(
            f"No context length field found in GGUF metadata for {file_path}"
        )
        return None

    except ImportError:
        logger.error(
            "gguf library not available - cannot extract metadata from GGUF files"
        )
        return None
    except Exception as e:
        logger.warning(f"Failed to extract training context from GGUF: {e}")
        return None


async def get_training_context(model_id: str) -> int | None:
    """
    Get training_context_length from catalog (single source of truth).

    The catalog is the authoritative source for model metadata. If a model
    is not in the catalog or the catalog doesn't have training_context_length,
    this function returns None to indicate the gateway needs to be updated.

    Args:
        model_id: Model identifier from catalog

    Returns:
        Training context length from catalog, or None if not found
    """
    # Check if model_id is an absolute file path (for backwards compatibility)
    potential_path = Path(model_id)
    if (
        potential_path.is_absolute()
        and potential_path.exists()
        and potential_path.is_file()
    ):
        logger.warning(
            f"Using absolute file path as model_id is deprecated: {model_id}. "
            "Use catalog ID instead."
        )
        return extract_training_context_from_gguf(potential_path)

    # Catalog is the single source of truth
    try:
        from ..core.catalog import get_catalog_loader

        loader = get_catalog_loader()
        model = loader.get_model(model_id)

        if not model:
            # Model not in catalog - gateway may need reload
            logger.warning(
                f"Model '{model_id}' not found in catalog. "
                "If recently added, restart gateway to reload catalog."
            )
            return None

        metadata = model.get("metadata", {})
        training_ctx = metadata.get("training_context_length")

        if not training_ctx:
            # Entry exists but missing required field
            logger.error(
                f"Catalog entry for '{model_id}' missing training_context_length. "
                "Update catalog with correct metadata."
            )
            return None

        return training_ctx

    except Exception as e:
        logger.error(f"Failed to access catalog for model '{model_id}': {e}")
        return None


def resolve_model_path(model_id: str) -> Path | None:
    """Resolve model ID to file path."""
    try:
        from ..core.catalog import get_catalog_loader

        loader = get_catalog_loader()
        model = loader.get_model(model_id)

        if model:
            download = model.get("download", {})
            hf_info = download.get("huggingface", {})
            filename = hf_info.get("file")

            if filename:
                model_root = Path(
                    os.environ.get("MODEL_PATH_ROOT", "/mnt/torus/models")
                )
                path = model_root / filename
                if path.exists():
                    return path
    except Exception as e:
        logger.debug(f"Catalog lookup failed: {e}")

    return _find_model_by_pattern(model_id)


def _find_model_by_pattern(model_id: str) -> Path | None:
    """Find model file by common naming patterns."""
    model_root = Path(os.environ.get("MODEL_PATH_ROOT", "/mnt/torus/models"))

    patterns = [
        f"{model_id}.gguf",
        f"{model_id.replace('-', '_')}.gguf",
        model_id,
    ]

    for pattern in patterns:
        path = model_root / pattern
        if path.exists():
            return path

    return None


def determine_activated_contexts(
    results: dict[str, dict[str, Any]], mode: str
) -> tuple[list[int], list[int], str]:
    """
    Determine activated_gpu_contexts and activated_cpu_contexts from results.

    For GPU mode:
      - Activate the largest context that fits entirely on GPU (n_gpu_layers=-1)
      - If none fit entirely, activate the largest successful context
    For CPU mode:
      - Activate the largest successful CPU context

    Returns:
        (activated_gpu_contexts, activated_cpu_contexts, activation_reason)
    """
    # Separate GPU and CPU results
    # Skip profiles that exceed resource caps (exceeds_cap=True)
    gpu_full_offload: list[int] = []  # contexts with n_gpu_layers=-1
    gpu_partial: list[int] = []  # contexts with partial offload
    cpu_contexts: list[int] = []

    for ctx_str, profile in results.items():
        if profile.get("error") or not profile.get("success", True):
            continue
        # Skip profiles that exceed resource caps
        if profile.get("exceeds_cap"):
            continue

        ctx = int(ctx_str)
        n_layers = profile.get("n_gpu_layers", 0)

        if n_layers == -1:
            gpu_full_offload.append(ctx)
        elif n_layers == 0:
            cpu_contexts.append(ctx)
        else:
            gpu_partial.append(ctx)

    # Determine which context to activate
    activated_gpu: list[int] = []
    activated_cpu: list[int] = []
    activation_reason = ""

    if mode in ("gpu", "auto"):
        if gpu_full_offload:
            # Prefer largest context that fits entirely on GPU
            best_ctx = max(gpu_full_offload)
            activated_gpu = [best_ctx]
            activation_reason = f"GPU context {best_ctx} (full offload)"
        elif gpu_partial:
            # Fall back to largest partial offload
            best_ctx = max(gpu_partial)
            activated_gpu = [best_ctx]
            activation_reason = (
                f"GPU context {best_ctx} (partial offload, no full-GPU fit)"
            )

    if mode in ("cpu", "auto") and cpu_contexts:
        best_ctx = max(cpu_contexts)
        activated_cpu = [best_ctx]
        if activation_reason:
            activation_reason += f", CPU context {best_ctx}"
        else:
            activation_reason = f"CPU context {best_ctx}"

    return activated_gpu, activated_cpu, activation_reason


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

    V2 Format:
        - Writes to model["devices"][device]["profiles"][context]
        - No config_type parameter
        - Device determined from n_gpu_layers if not provided:
          - n_gpu_layers == 0 or None → "cpu"
          - n_gpu_layers == -1 → "gpu"
          - n_gpu_layers > 0 → "hybrid" (if supported by schema; else "gpu")

    Returns True if successful, False otherwise.
    """
    try:
        from inference_djinn.catalog.local_config import (
            load_local_catalog,
            save_local_catalog,
        )

        from ..core.catalog.schemas import SchemaRegistry

        catalog = load_local_catalog()
        models = catalog.get("models", {})

        if model_id not in models:
            logger.warning(f"Model {model_id} not in local catalog")
            return False

        model = models[model_id]

        # V2 strictness: schema field must exist (no format-based fallback)
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

        # Determine device if not provided
        if device is None:
            if n_gpu_layers is None or n_gpu_layers == 0:
                device = "cpu"
            elif n_gpu_layers == -1:
                device = "gpu"
            else:
                device = "hybrid" if "hybrid" in schema.supported_devices else "gpu"

        # Validate device is supported
        if device not in schema.supported_devices:
            logger.warning(
                f"Device '{device}' not supported by schema '{schema_name}', "
                f"skipping profile update for {model_id}"
            )
            return False

        devices = model.setdefault("devices", {})
        device_config = devices.setdefault(device, {})
        profiles = device_config.setdefault("profiles", {})

        # Build profile entry
        profile_entry: dict[str, Any] = {
            "vram_mb": vram_mb or 0,
            "ram_mb": ram_mb or 0,
        }

        # Engine-specific fields (native GGUF engine uses n_gpu_layers)
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
        logger.warning(f"Failed to update profile: {e}")
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
        # Optional: trigger non-blocking remote sync
        schedule_remote_sync_nowait(model_id, "contexts")
        return True

    except ImportError:
        logger.warning("Local catalog module not available")
        return False
    except Exception as e:
        logger.warning(f"Failed to update activated contexts: {e}")
        return False


def schedule_remote_sync_nowait(model_id: str, update_type: str) -> None:
    """
    Schedule optional remote catalog sync (fire-and-forget).

    Non-blocking: creates background task with short timeout.
    Currently disabled (REMOTE_SYNC_ENABLED=False).

    TODO: Implement actual remote sync when central catalog service is available.
    Expected flow:
    1. POST updated profile/contexts to remote catalog endpoint
    2. Remote catalog validates and stores
    3. Other nodes can pull updates

    Args:
        model_id: Model identifier
        update_type: Type of update ("profile" or "contexts")
    """
    if not REMOTE_SYNC_ENABLED:
        return

    async def _sync_with_timeout() -> None:
        try:
            async with asyncio.timeout(REMOTE_SYNC_TIMEOUT_MS / 1000):
                # TODO: Implement actual HTTP POST to remote catalog
                # Example:
                # async with httpx.AsyncClient() as client:
                #     await client.post(
                #         f"{REMOTE_CATALOG_URL}/api/v1/models/{model_id}/sync",
                #         json={"update_type": update_type}
                #     )
                logger.debug(f"Remote sync stub: {model_id}/{update_type}")
        except TimeoutError:
            logger.warning(f"Remote sync timed out: {model_id}/{update_type}")
        except Exception as e:
            logger.warning(f"Remote sync failed: {model_id}/{update_type}: {e}")

    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_sync_with_timeout())
        task.add_done_callback(lambda t: None)  # Suppress unhandled task warnings
    except RuntimeError:
        # No running event loop (sync context) - skip remote sync
        pass
