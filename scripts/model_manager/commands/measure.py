"""Measurement commands for profiling model resource usage."""

import argparse
import os
import sys
from typing import Any

import requests
from universal_logging import get_logger

from ..config import Config
from .measure_sources import GGUF_FORMAT, VLLM_FORMATS, get_models_to_remeasure
from .measure_streaming import check_job_status, stream_job_logs

# Default Stargate URL for gateway discovery
DEFAULT_STARGATE_URL = "http://localhost:9999"

logger = get_logger(__name__)


def cmd_measure(args: argparse.Namespace, config: Config) -> int:
    """Trigger measurement job on Gateway via Job API."""
    # Parse contexts: None means auto-detect from model metadata
    parsed = _parse_contexts(args.contexts)
    if parsed == "error":
        return 1
    contexts: list[int] | None = None if parsed is None else parsed  # type: ignore[assignment]

    mode = "cpu" if args.cpu else "gpu"

    # Resource caps (GiB -> MB)
    vram_cap_mb = getattr(args, "vram_cap", None)
    ram_cap_mb = getattr(args, "ram_cap", None)
    if vram_cap_mb is not None:
        vram_cap_mb = vram_cap_mb * 1024  # GiB to MB
    if ram_cap_mb is not None:
        ram_cap_mb = ram_cap_mb * 1024  # GiB to MB

    # Determine Stargate URL (for federation routing)
    stargate_url = getattr(args, "stargate", None) or os.getenv(
        "STARGATE_URL", DEFAULT_STARGATE_URL
    )

    # Validate Stargate is reachable and has local Edge
    gateway_result = _select_best_gateway(stargate_url, args.model_id, mode)
    if gateway_result is None:
        return 1

    gateway_id, gateway_info = gateway_result
    print(f"Using Stargate: {stargate_url}")
    print(f"  Target gateway: {gateway_id}")
    print(f"  VRAM: {gateway_info['total_vram_mb']}MB total")

    # Prepare API credentials (used for pre-flight check and measurement)
    api_key = getattr(args, "gateway_api_key", None)
    headers = _auth_headers(api_key)
    request_timeout = getattr(args, "timeout", None) or 10

    # Pre-flight check: detect loaded models before measurement
    # Measurements require clean resources for accurate capacity detection
    if mode in ("gpu", "cpu"):
        loaded_models = _check_loaded_models(stargate_url, headers, request_timeout)
        if loaded_models:
            # Adjust message based on mode
            resource_type = "VRAM" if mode == "gpu" else "RAM"
            print(
                f"\n⚠️  {resource_type} OCCUPIED: {len(loaded_models)} model(s) currently loaded:",
                file=sys.stderr,
            )
            for model_id in loaded_models:
                print(f"   - {model_id}", file=sys.stderr)
            print(
                f"\n{mode.upper()} measurement requires clean {resource_type} to accurately detect capacity.",
                file=sys.stderr,
            )
            print(
                "Loaded models will cause false OOM failures or hangs.\n",
                file=sys.stderr,
            )
            print("Options:", file=sys.stderr)
            print("  1. Unload models via API:", file=sys.stderr)
            for model_id in loaded_models:
                print(
                    f"     curl -X DELETE {stargate_url}/gateway/models/{model_id}",
                    file=sys.stderr,
                )
            print(
                f"  2. Restart Gateway to clear all {resource_type}\n", file=sys.stderr
            )

            # Prompt user for action
            try:
                response = input("Unload all models now? [y/N]: ").strip().lower()
                if response in ("y", "yes"):
                    print("\nUnloading models...")
                    if _unload_all_models(
                        stargate_url, loaded_models, headers, request_timeout
                    ):
                        print("✅ All models unloaded successfully\n")
                    else:
                        print(
                            "❌ Failed to unload some models. Aborting measurement.",
                            file=sys.stderr,
                        )
                        return 1
                else:
                    resource_type = "VRAM" if mode == "gpu" else "RAM"
                    print(
                        f"❌ Measurement aborted. Please clear {resource_type} and retry.",
                        file=sys.stderr,
                    )
                    return 1
            except (EOFError, KeyboardInterrupt):
                resource_type = "VRAM" if mode == "gpu" else "RAM"
                print(
                    f"\n❌ Measurement aborted. Please clear {resource_type} and retry.",
                    file=sys.stderr,
                )
                return 1

    print(f"\nStarting measurement for {args.model_id}...")
    if contexts is None:
        print("  Contexts: auto-detect from training_context_length")
    else:
        print(f"  Contexts: {contexts}")
    print(f"  Mode: {mode}")
    if vram_cap_mb:
        print(f"  VRAM cap: {vram_cap_mb}MB ({vram_cap_mb // 1024}GB)")
    if ram_cap_mb:
        print(f"  RAM cap: {ram_cap_mb}MB ({ram_cap_mb // 1024}GB)")
    print()

    enable_hybrid = getattr(args, "enable_hybrid", True)
    job = _start_measurement_job(
        stargate_url,
        args,
        contexts,
        mode,
        headers,
        request_timeout,
        vram_cap_mb,
        ram_cap_mb,
        enable_hybrid,
    )
    if job is None:
        return 1

    job_id = job.get("job_id", "unknown")
    print(f"Job started: {job_id}")
    print(f"Streaming logs from /gateway/jobs/{job_id}/logs...")
    print()

    verbose = getattr(args, "verbose", False) or config.verbose
    if not stream_job_logs(stargate_url, job_id, headers, verbose):
        return 130

    exit_code = check_job_status(stargate_url, job_id, headers, request_timeout)
    if exit_code != 0:
        return exit_code

    # Auto-update catalog if requested
    if getattr(args, "update_catalog", False):
        static_mode = getattr(args, "static", False)
        mmproj_path = getattr(args, "mmproj", None)
        tokens_per_image = getattr(args, "tokens_per_image", None)

        # CLI handles all catalog updates (static writes to host filesystem, dynamic via API)
        print("\n📝 Updating catalog with measurement results...")
        if not _update_catalog_after_measurement(
            stargate_url,
            job_id,
            args.model_id,
            headers,
            request_timeout,
            static_mode,
            mmproj_path,
            getattr(args, "vision_architecture", None),
            tokens_per_image,
        ):
            print("⚠️  Measurement succeeded but catalog update failed", file=sys.stderr)
            return 1
        print("✅ Catalog updated successfully")

    return 0


def _select_best_gateway(  # noqa: PLR0912
    stargate_url: str, model_id: str, mode: str
) -> tuple[str, dict] | None:
    """
    Query Stargate for gateway status and select best gateway for measurement.

    For measurement, we don't require the model to be loaded - just need:
    1. A healthy gateway
    2. Sufficient resources (VRAM/RAM based on mode)

    The model file existence is verified by the Gateway's measurement job.

    Returns (gateway_id, gateway_info) tuple or None on error.
    """
    try:
        response = requests.get(
            f"{stargate_url}/api/v1/gateways/status/full",
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except requests.ConnectionError:
        print(
            f"❌ Cannot connect to Stargate at {stargate_url}",
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        if stargate_url == DEFAULT_STARGATE_URL:
            print(
                "   Stargate is required for multi-gateway management.",
                file=sys.stderr,
            )
            print("   Either:", file=sys.stderr)
            print("     1. Start Stargate on localhost:9999", file=sys.stderr)
            print(
                "     2. Set STARGATE_URL environment variable",
                file=sys.stderr,
            )
            print(
                "     3. Use --gateway URL to specify gateway directly",
                file=sys.stderr,
            )
        return None
    except requests.RequestException as e:
        print(f"❌ Failed to query Stargate: {e}", file=sys.stderr)
        return None

    gateways = data.get("gateways", {})
    if not gateways:
        print("❌ No gateways configured in Stargate", file=sys.stderr)
        return None

    # For measurement, just find connected gateways (model doesn't need to be loaded)
    candidates = []
    for gateway_id, gw in gateways.items():
        if not gw.get("is_connected"):
            continue
        candidates.append((gateway_id, gw))

    if not candidates:
        print("❌ No connected gateways available", file=sys.stderr)
        return None

    # Select best gateway based on mode
    if mode == "gpu":
        # Prefer most VRAM for GPU measurement
        best = max(candidates, key=lambda x: x[1].get("total_vram_mb", 0))
    else:
        # Prefer most RAM for CPU measurement
        best = max(candidates, key=lambda x: x[1].get("total_ram_mb", 0))

    return best


def _parse_contexts(contexts_str: str | None) -> list[int] | None | str:
    """
    Parse comma-separated context string to list of ints.

    Returns:
        None: auto-detect from model metadata (no --contexts specified)
        list[int]: explicit contexts
        "error": parse error
    """
    if not contexts_str:
        return None  # Auto-detect from metadata
    try:
        return [int(c.strip()) for c in contexts_str.split(",")]
    except ValueError:
        print(
            "❌ Invalid --contexts format. Use comma-separated integers",
            file=sys.stderr,
        )
        return "error"


def _start_measurement_job(  # noqa: PLR0913
    stargate_url: str,
    args: argparse.Namespace,
    contexts: list[int] | None,
    mode: str,
    headers: dict[str, str],
    request_timeout: float,
    vram_cap_mb: int | None = None,
    ram_cap_mb: int | None = None,
    enable_hybrid: bool = True,
) -> dict | None:
    """Start measurement job via Stargate /gateway/* API."""
    payload: dict = {
        "type": "measure",
        "model_id": args.model_id,
        "mode": mode,
        "gpu_index": args.gpu_index,
    }
    # Only include contexts if explicitly specified (None = auto-detect)
    if contexts is not None:
        payload["contexts"] = contexts
    # Resource caps for "fits" determination
    if vram_cap_mb is not None:
        payload["vram_cap_mb"] = vram_cap_mb
    if ram_cap_mb is not None:
        payload["ram_cap_mb"] = ram_cap_mb
    if enable_hybrid:
        payload["enable_hybrid"] = True

    # Safety margin for hybrid mode
    safety_margin = getattr(args, "safety_margin", None)
    if safety_margin is not None:
        payload["safety_margin"] = safety_margin

    # Vision model support
    mmproj_path = getattr(args, "mmproj", None)
    if mmproj_path:
        payload["mmproj_path"] = mmproj_path

    # Pass tokens_per_image if provided
    tokens_per_image = getattr(args, "tokens_per_image", None)
    if tokens_per_image is not None:
        payload["tokens_per_image"] = tokens_per_image

    try:
        response = requests.post(
            f"{stargate_url}/gateway/jobs",
            json=payload,
            headers=headers,
            timeout=request_timeout,
        )
        response.raise_for_status()
        return response.json()
    except requests.ConnectionError:
        print(f"❌ Cannot connect to Stargate at {stargate_url}", file=sys.stderr)
        print(
            "   Make sure Stargate is running with local Edge configured.",
            file=sys.stderr,
        )
        return None
    except requests.RequestException as e:
        print(f"❌ Failed to start measurement job: {e}", file=sys.stderr)
        return None


def cmd_remeasure(args: argparse.Namespace, config: Config) -> int:  # noqa: PLR0912
    """
    Re-measure profiles for existing models.

    By default measures:
    - GGUF (llama-cpp-python): GPU + CPU modes
    - vLLM (hf/awq/gptq): GPU only (no CPU/hybrid support)

    Uses smart context detection:
    - GPU mode: steps down from training_context_length until it fits
    - CPU mode: uses training_context_length (GGUF only)
    - Auto: tries GPU first with step-down, falls back to CPU (GGUF only)
    """
    models = get_models_to_remeasure(args, config)
    if models is None:
        return 1
    if not models:
        print("No models found to measure")
        return 0

    # Count by format
    gguf_count = sum(1 for _, fmt in models if fmt == GGUF_FORMAT)
    vllm_count = sum(1 for _, fmt in models if fmt in VLLM_FORMATS)

    # Determine mode description
    if args.cpu:
        mode_desc = "CPU only"
    else:
        mode_desc = "GPU (default, with hybrid offload)"

    print(f"Remeasuring {len(models)} model(s)...")
    print(f"  GGUF models: {gguf_count}")
    print(f"  vLLM models: {vllm_count}")
    print(f"  Mode: {mode_desc}")
    if args.contexts:
        print(f"  Contexts: {args.contexts} (explicit)")
    else:
        print("  Contexts: auto-detect from training_context_length")
    print()

    failed = []
    for i, (model_id, model_format) in enumerate(models, 1):
        print(f"\n{'=' * 60}")
        print(f"[{i}/{len(models)}] Model: {model_id} ({model_format})")
        print(f"{'=' * 60}")

        # Determine mode for this model
        # vLLM models: GPU only (no CPU support)
        if model_format in VLLM_FORMATS:
            if args.cpu and not args.gpu:
                # CPU-only mode requested, but vLLM doesn't support it
                print("  ⚠️  Skipping: vLLM models do not support CPU-only mode")
                continue
            # vLLM always uses GPU (ignore --cpu when --gpu also set)
            use_gpu = True
            use_cpu = False
        else:
            # GGUF: respect user flags
            use_gpu = args.gpu
            use_cpu = args.cpu

        measure_args = argparse.Namespace(
            model_id=model_id,
            contexts=args.contexts,  # None = auto-detect
            gpu=use_gpu,
            cpu=use_cpu,
            stargate=getattr(args, "stargate", None),
            gpu_index=getattr(args, "gpu_index", 0),
            vram_cap=getattr(args, "vram_cap", None),
            ram_cap=getattr(args, "ram_cap", None),
            gateway_api_key=getattr(args, "gateway_api_key", None),
            timeout=getattr(args, "timeout", None),
            enable_hybrid=getattr(args, "enable_hybrid", True),
            mmproj=getattr(args, "mmproj", None),
            vision_architecture=getattr(args, "vision_architecture", None),
            tokens_per_image=getattr(args, "tokens_per_image", None),
            update_catalog=True,  # Always update catalog in remeasure
            static=getattr(args, "static", False),
            verbose=getattr(args, "verbose", False),
        )

        result = cmd_measure(measure_args, config)
        if result != 0:
            failed.append(model_id)

    print(f"\n{'=' * 60}")
    print(f"Summary: {len(models) - len(failed)}/{len(models)} succeeded")
    if failed:
        print(f"Failed: {', '.join(failed)}")
        return 1
    return 0


def _check_loaded_models(
    stargate_url: str, headers: dict[str, str], timeout: float
) -> list[str]:
    """Check if any models are currently loaded via Stargate."""
    try:
        response = requests.get(
            f"{stargate_url}/gateway/status/resources",
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("loaded_models", [])
    except requests.RequestException as e:
        print(f"⚠️  Could not check loaded models: {e}", file=sys.stderr)
        return []


def _unload_all_models(
    stargate_url: str,
    model_ids: list[str],
    headers: dict[str, str],
    timeout: float,
) -> bool:
    """Unload all specified models via Stargate."""
    success = True
    for model_id in model_ids:
        try:
            response = requests.delete(
                f"{stargate_url}/gateway/models/{model_id}",
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            status = data.get("status")
            if status == "unloaded":
                print(f"   ✓ {model_id}: unloaded")
            elif status == "not_loaded":
                print(f"   ✓ {model_id}: already unloaded")
            elif status == "skipped":
                reason = data.get("reason", "unknown")
                print(f"   ⚠️  {model_id}: skipped ({reason})")
                success = False
            else:
                print(f"   ❌ {model_id}: unexpected status: {status}")
                success = False
        except requests.RequestException as e:
            print(f"   ❌ {model_id}: failed to unload: {e}")
            success = False

    return success


def _auth_headers(api_key: str | None) -> dict[str, str]:
    """Build auth headers for Gateway requests."""
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def _detect_vision_architecture(model_id: str) -> str | None:
    """
    Detect vision architecture from model ID/name.

    Returns:
        Vision architecture key (e.g., 'qwen2_vl') or None if not detected
    """
    model_lower = model_id.lower()

    # Vision model patterns
    if "qwen2" in model_lower and "vl" in model_lower:
        return "qwen2_vl"
    elif "llava" in model_lower:
        if "1.6" in model_lower or "next" in model_lower:
            return "llava_1_6"
        else:
            return "llava_1_5"
    elif "minicpm" in model_lower and "v" in model_lower:
        return "minicpm_v"
    elif "moondream" in model_lower:
        return "moondream"
    elif "ministral" in model_lower or "mistral-3" in model_lower:
        return "mistral3"

    return None


def _reload_gateway_catalog(
    stargate_url: str,
    headers: dict[str, str],
    request_timeout: float,
) -> bool:
    """
    Trigger catalog reload on Gateway via Stargate.

    When static catalog is updated on host, Gateway container needs to reload
    to see the new/updated models.

    Returns:
        True if reload succeeded, False otherwise
    """
    try:
        # Note: This endpoint doesn't exist yet through Stargate proxy
        # For now, we directly call the Gateway endpoint via container
        # TODO: Add /gateway/catalog/reload proxy endpoint to Stargate
        response = requests.post(
            f"{stargate_url}/gateway/catalog/reload",
            headers=headers,
            timeout=max(request_timeout, 10),
        )
        response.raise_for_status()
        logger.info("Triggered catalog reload on Gateway")
        return True
    except requests.RequestException as e:
        logger.warning(f"Failed to reload Gateway catalog: {e}")
        return False


def _update_catalog_after_measurement(
    stargate_url: str,
    job_id: str,
    model_id: str,
    headers: dict[str, str],
    request_timeout: float,
    static: bool = False,
    mmproj_path: str | None = None,
    vision_architecture: str | None = None,
    tokens_per_image: int | None = None,
) -> bool:
    """
    Fetch measurement result and update catalog.

    For static catalog: writes directly to config/models/ (host filesystem)
    For dynamic catalog: calls Gateway API
    """
    # Schemas that support hybrid (partial GPU offload)
    schemas_with_hybrid = {"llama-cpp"}

    try:
        # Fetch job result via Stargate
        response = requests.get(
            f"{stargate_url}/gateway/jobs/{job_id}",
            headers=headers,
            timeout=max(request_timeout, 5),
        )
        response.raise_for_status()
        job_data = response.json()

        result = job_data.get("result")
        if not result:
            print("❌ No result data in job response", file=sys.stderr)
            return False

        # Get current catalog entry via Stargate API
        response = requests.get(
            f"{stargate_url}/gateway/models/{model_id}/config",
            headers=headers,
            timeout=max(request_timeout, 5),
        )
        response.raise_for_status()
        catalog_entry = response.json().get("config", {})

        if not catalog_entry:
            print(f"❌ Model {model_id} not found in catalog", file=sys.stderr)
            return False

        catalog_entry = _build_updated_catalog_entry(
            catalog_entry,
            result,
            model_id,
            mmproj_path,
            vision_architecture,
            tokens_per_image,
            schemas_with_hybrid,
        )
        if catalog_entry is None:
            return False

        if static:
            # Write directly to host filesystem (no API call)
            from universal_logging import get_logger

            from .catalog_writer import write_static_catalog_entry

            logger = get_logger(__name__)

            try:
                file_path, operation = write_static_catalog_entry(
                    model_id, catalog_entry, allow_overwrite=True
                )
                print(f"   {operation.title()} static catalog: {file_path}")
                
                # Reload Gateway catalog to reflect filesystem changes
                print("   Reloading Gateway catalog...")
                if not _reload_gateway_catalog(stargate_url, headers, request_timeout):
                    print(
                        "   ⚠️  Catalog reload failed - Gateway may not see updates",
                        file=sys.stderr,
                    )
                
                return True
            except Exception as e:
                logger.error(f"Failed to write static catalog for {model_id}: {e}")
                print(f"❌ Failed to write static catalog: {e}", file=sys.stderr)
                return False
        else:
            # Dynamic catalog: use API (existing behavior)
            payload = {
                "model_key": model_id,
                "config": catalog_entry,
                "allow_overwrite": True,
                "static": False,  # Explicit: dynamic only via API
            }
            response = requests.post(
                f"{stargate_url}/gateway/models",
                json=payload,
                headers=headers,
                timeout=max(request_timeout, 10),
            )
            response.raise_for_status()
            print(f"   Updated dynamic catalog for {model_id}")
            return True

    except requests.HTTPError as e:
        print(f"❌ HTTP error updating catalog: {e}", file=sys.stderr)
        if hasattr(e, "response") and e.response:
            print(f"   Response: {e.response.text[:200]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ Error updating catalog: {e}", file=sys.stderr)
        return False


def _build_updated_catalog_entry(
    catalog_entry: dict[str, Any],
    result: dict[str, Any],
    model_id: str,
    mmproj_path: str | None,
    vision_architecture: str | None,
    tokens_per_image: int | None,
    schemas_with_hybrid: set[str],
) -> dict[str, Any] | None:
    """Build updated catalog entry with measurement profiles."""
    metadata = catalog_entry.setdefault("metadata", {})
    model_format = metadata.get("format", "gguf")

    # V2: Schema field REQUIRED (fail-fast, no derivation)
    schema_name = catalog_entry.get("schema")
    if not schema_name:
        print(
            f"❌ Model {model_id} missing required 'schema' field (V2 catalog format required)",
            file=sys.stderr,
        )
        print(
            "   Add 'schema' field to catalog entry (e.g., 'llama-cpp', 'vllm', etc.)",
            file=sys.stderr,
        )
        return None

    # Remove engine from metadata if present (V2: derived from schema)
    if "engine" in metadata:
        del metadata["engine"]

    # Add vision model metadata if mmproj provided
    if mmproj_path:
        vision_arch = vision_architecture or _detect_vision_architecture(model_id)
        if vision_arch:
            metadata["is_vision_model"] = True
            metadata["vision_architecture"] = vision_arch
            if tokens_per_image is not None:
                metadata["tokens_per_image"] = tokens_per_image
                print(f"   Setting tokens_per_image: {tokens_per_image}")
            if vision_architecture:
                print(f"   Using vision architecture: {vision_arch}")
            else:
                print(f"   Detected vision architecture: {vision_arch}")

    # V2: Use 'loader' instead of top-level 'base_loader'
    loader = catalog_entry.setdefault("loader", {})

    # Set default loader params if not present (based on format)
    if model_format == "gguf" and not loader:
        loader.update(
            {
                "f16_kv": True,
                "use_mmap": False,
                "use_mlock": True,
                "verbose": False,
                "n_batch": 512,
            }
        )
    elif model_format in ("hf", "awq", "gptq") and not loader:
        loader.update(
            {
                "trust_remote_code": False,
                "disable_custom_all_reduce": True,
                "disable_log_stats": True,
            }
        )

    # Handle vision parameters in loader
    if mmproj_path:
        vision_arch = vision_architecture or _detect_vision_architecture(model_id)
        if vision_arch:
            loader["vision_architecture"] = vision_arch
            loader["clip_model_path"] = mmproj_path
            if tokens_per_image is not None:
                loader["tokens_per_image"] = tokens_per_image
            print("   Adding vision parameters to loader")

    # V2: Use 'devices' instead of 'configurations'
    devices = catalog_entry.setdefault("devices", {})

    # Separate profiles by device type (inferred from n_gpu_layers)
    all_profiles = result.get("profiles", {})
    gpu_profiles: dict[str, dict[str, Any]] = {}
    cpu_profiles: dict[str, dict[str, Any]] = {}
    hybrid_profiles: dict[str, dict[str, Any]] = {}

    for ctx_str, profile in all_profiles.items():
        if profile.get("error") or not profile.get("success", True):
            continue

        n_gpu_layers = profile.get("n_gpu_layers")

        # Determine device from n_gpu_layers
        if n_gpu_layers is None or n_gpu_layers == 0:
            device = "cpu"
        elif n_gpu_layers == -1:
            device = "gpu"
        else:
            device = "hybrid" if schema_name in schemas_with_hybrid else "gpu"

        # Remove internal/status/timing fields before storing
        clean_profile = {
            k: v
            for k, v in profile.items()
            if k
            not in [
                "success",
                "error",
                "exceeds_cap",
                "cap_exceeded_reason",
                "total_layers",
                # Timing/debugging fields (not part of schema)
                "stderr",
                "load_time_sec",
                "warmup_time_sec",
                "total_time_sec",
            ]
        }

        if device == "cpu":
            cpu_profiles[ctx_str] = clean_profile
        elif device == "hybrid":
            hybrid_profiles[ctx_str] = clean_profile
        else:
            gpu_profiles[ctx_str] = clean_profile

    # Merge new profiles into existing (don't replace entire profiles dict)
    if gpu_profiles:
        gpu_device = devices.setdefault("gpu", {})
        existing = gpu_device.get("profiles", {})
        existing.update(gpu_profiles)
        gpu_device["profiles"] = existing

    if cpu_profiles:
        cpu_device = devices.setdefault("cpu", {})
        existing = cpu_device.get("profiles", {})
        existing.update(cpu_profiles)
        cpu_device["profiles"] = existing
        if model_format == "gguf" and not gpu_profiles and not hybrid_profiles:
            loader.update({"f16_kv": False, "use_mmap": True, "use_mlock": False})

    if hybrid_profiles:
        hybrid_device = devices.setdefault("hybrid", {})
        existing = hybrid_device.get("profiles", {})
        existing.update(hybrid_profiles)
        hybrid_device["profiles"] = existing

    # Update activated contexts in metadata
    if gpu_profiles or cpu_profiles or hybrid_profiles:
        if cpu_profiles:
            cpu_contexts = [int(ctx) for ctx in cpu_profiles.keys()]
            if cpu_contexts:
                metadata["activated_cpu_contexts"] = [max(cpu_contexts)]

        if gpu_profiles or hybrid_profiles:
            gpu_contexts = [int(ctx) for ctx in gpu_profiles.keys()]
            hybrid_contexts = [int(ctx) for ctx in hybrid_profiles.keys()]
            all_gpu_contexts = sorted(gpu_contexts + hybrid_contexts, reverse=True)
            if all_gpu_contexts:
                metadata["activated_gpu_contexts"] = all_gpu_contexts

    # Remove V1 keys if present (cleanup)
    catalog_entry.pop("configurations", None)
    catalog_entry.pop("base_loader", None)

    return catalog_entry
