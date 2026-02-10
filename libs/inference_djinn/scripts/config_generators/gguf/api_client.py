"""
API Client

Handles pushing configurations to the universal-llm-gateway API.
"""

import sys

try:
    import requests
except ImportError:
    requests = None


def push_profile_update(
    model_id: str,
    context: int,
    config_type: str,
    vram_mb: int | None = None,
    ram_mb: int | None = None,
    n_gpu_layers: int | None = None,
    api_url: str = "http://localhost:9998",
    api_token: str | None = None,
) -> bool:
    """
    Push a profile update via the catalog PATCH endpoint.

    Updates VRAM/RAM values for a specific model profile using the new
    catalog API that exports models to local catalog for customization.

    Args:
        model_id: Model identifier
        context: Context length (e.g., 4096, 8192)
        config_type: Configuration type (gpu-batch512, cpu-batch256, vllm-default)
        vram_mb: VRAM usage in MB (optional)
        ram_mb: RAM usage in MB (optional)
        n_gpu_layers: Number of GPU layers (optional)
        api_url: Gateway API base URL
        api_token: Optional authentication token

    Returns:
        True if successful, False otherwise
    """
    if requests is None:
        print("Error: 'requests' library not installed", file=sys.stderr)
        return False

    url = f"{api_url}/api/v1/catalog/models/{model_id}/profile"
    headers = {"Content-Type": "application/json"}

    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    payload: dict[str, int | str] = {
        "context": context,
        "config_type": config_type,
    }
    if vram_mb is not None:
        payload["vram_mb"] = vram_mb
    if ram_mb is not None:
        payload["ram_mb"] = ram_mb
    if n_gpu_layers is not None:
        payload["n_gpu_layers"] = n_gpu_layers

    print(f"Updating profile: {model_id} ({config_type}@{context})", file=sys.stderr)

    try:
        response = requests.patch(url, json=payload, headers=headers, timeout=30)

        if response.status_code == 200:
            result = response.json()
            updated = result.get("updated_fields", [])
            print(f"  ✅ Updated fields: {updated}", file=sys.stderr)
            return True
        else:
            print(
                f"Error: API returned {response.status_code}: {response.text}",
                file=sys.stderr,
            )
            return False

    except requests.exceptions.ConnectionError:
        print(f"Error: Could not connect to {api_url}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error: Push failed: {e}", file=sys.stderr)
        return False


# Backward compatibility alias for main.py --push-to-api
def push_profile_to_api(
    whole_profile,
    model_key: str,
    api_url: str,
    api_token: str | None = None,
) -> bool:
    """DEPRECATED: Use push_profile_update() instead."""
    print("⚠️  push_profile_to_api deprecated.", file=sys.stderr)
    configs = whole_profile.to_dict().get("configurations", {})
    success = True
    for config_name, config_data in configs.items():
        parts = config_name.rsplit("-", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        model_params = config_data.get("model_params", {})
        if not push_profile_update(
            model_id=model_key,
            context=int(parts[1]),
            config_type=parts[0],
            vram_mb=model_params.get("vram_mb"),
            ram_mb=model_params.get("ram_mb"),
            n_gpu_layers=model_params.get("n_gpu_layers"),
            api_url=api_url,
            api_token=api_token,
        ):
            success = False
    return success


def push_activated_contexts(
    model_id: str,
    activated_gpu_contexts: list[int] | None = None,
    activated_cpu_contexts: list[int] | None = None,
    api_url: str = "http://localhost:9998",
    api_token: str | None = None,
) -> bool:
    """
    Push activated contexts update via the catalog API.

    Args:
        model_id: Model identifier
        activated_gpu_contexts: GPU context lengths to expose in /v1/models
        activated_cpu_contexts: CPU context lengths to expose in /v1/models
        api_url: Gateway API base URL
        api_token: Optional authentication token

    Returns:
        True if successful, False otherwise
    """
    if requests is None:
        print("Error: 'requests' library not installed", file=sys.stderr)
        return False

    url = f"{api_url}/api/v1/catalog/models/{model_id}/activated-contexts"
    headers = {"Content-Type": "application/json"}

    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    payload: dict[str, list[int]] = {}
    if activated_gpu_contexts is not None:
        payload["activated_gpu_contexts"] = activated_gpu_contexts
    if activated_cpu_contexts is not None:
        payload["activated_cpu_contexts"] = activated_cpu_contexts

    print(f"Updating activated contexts: {model_id}", file=sys.stderr)

    try:
        response = requests.patch(url, json=payload, headers=headers, timeout=30)

        if response.status_code == 200:
            result = response.json()
            updated = result.get("updated_fields", [])
            print(f"  ✅ Updated: {updated}", file=sys.stderr)
            return True
        else:
            print(
                f"Error: API returned {response.status_code}: {response.text}",
                file=sys.stderr,
            )
            return False

    except requests.exceptions.ConnectionError:
        print(f"Error: Could not connect to {api_url}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error: Push failed: {e}", file=sys.stderr)
        return False
