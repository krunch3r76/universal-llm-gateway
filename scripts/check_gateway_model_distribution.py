#!/usr/bin/env python3
"""
Check which gateways have a specific model loaded.

This script directly queries Gateway API endpoints to determine model distribution.
For sticky models, only ONE gateway should have the model loaded.
"""

import sys

import requests


def get_gateway_loaded_models(
    gateway_url: str, timeout: float = 5.0
) -> dict[str, list[str] | str]:
    """
    Query Gateway API to get loaded models.

    Args:
        gateway_url: Gateway URL (e.g., http://localhost:9998)
        timeout: Request timeout in seconds

    Returns:
        Dict with 'models' list or 'error' string
    """
    try:
        # Try internal status endpoint first (more detailed)
        response = requests.get(
            f"{gateway_url}/api/v1/status/detailed",
            timeout=timeout,
        )

        if response.status_code == 200:
            data = response.json()
            # Extract loaded models from status
            loaded = []
            if "loaded_models" in data:
                loaded = data["loaded_models"]
            elif "models" in data and isinstance(data["models"], dict):
                loaded = [
                    model_id
                    for model_id, info in data["models"].items()
                    if isinstance(info, dict) and info.get("status") == "loaded"
                ]
            return {"models": loaded}

        # Fallback to health endpoint
        response = requests.get(
            f"{gateway_url}/health",
            timeout=timeout,
        )

        if response.status_code == 200:
            data = response.json()
            loaded = data.get("loaded_models", [])
            return {"models": loaded}

        return {"error": f"HTTP {response.status_code}"}

    except requests.exceptions.Timeout:
        return {"error": "timeout"}
    except requests.exceptions.ConnectionError:
        return {"error": "connection_refused"}
    except Exception as e:
        return {"error": str(e)}


def check_model_on_gateway(
    gateway_url: str,
    model_id: str,
    gateway_name: str = None,
) -> dict[str, str | bool]:
    """
    Check if specific model is loaded on gateway.

    Args:
        gateway_url: Gateway URL to check
        model_id: Model ID to look for
        gateway_name: Optional display name for gateway

    Returns:
        Dict with check results
    """
    name = gateway_name or gateway_url
    print(f"\nChecking {name}...")

    result = get_gateway_loaded_models(gateway_url)

    if "error" in result:
        print(f"  ❌ Failed to connect: {result['error']}")
        return {
            "gateway": name,
            "url": gateway_url,
            "reachable": False,
            "has_model": False,
            "error": result["error"],
        }

    models = result["models"]
    has_model = model_id in models

    if has_model:
        print(f"  ✓ Model IS loaded ({len(models)} total models)")
    else:
        print(f"  ✗ Model NOT loaded ({len(models)} total models)")

    return {
        "gateway": name,
        "url": gateway_url,
        "reachable": True,
        "has_model": has_model,
        "loaded_models": models,
    }


def main() -> int:
    """Check model distribution across gateways."""
    if len(sys.argv) < 2:
        print("Usage: check_gateway_model_distribution.py MODEL_ID [GATEWAY_URL...]")
        print("")
        print("Examples:")
        print("  # Check localhost gateway (default)")
        print(
            "  ./scripts/check_gateway_model_distribution.py phi-3-5-mini-instruct-q8-0-16384"
        )
        print("")
        print("  # Check multiple gateways")
        print(
            "  ./scripts/check_gateway_model_distribution.py phi-3-5-mini-instruct-q8-0-16384 \\"
        )
        print("    http://localhost:9998 http://localhost:9997")
        print("")
        return 1

    model_id = sys.argv[1]
    gateway_urls = sys.argv[2:] if len(sys.argv) > 2 else ["http://localhost:9998"]

    print("=" * 80)
    print("GATEWAY MODEL DISTRIBUTION CHECK")
    print("=" * 80)
    print(f"\nModel: {model_id}")
    print(f"Checking {len(gateway_urls)} gateway(s)...")

    # Check each gateway
    results = []
    for i, url in enumerate(gateway_urls, 1):
        result = check_model_on_gateway(
            url,
            model_id,
            gateway_name=f"Gateway {i} ({url})",
        )
        results.append(result)

    # Analyze results
    print("\n" + "=" * 80)
    print("ANALYSIS")
    print("=" * 80)

    reachable = [r for r in results if r["reachable"]]
    with_model = [r for r in results if r.get("has_model", False)]

    if not reachable:
        print("\n❌ No gateways reachable")
        print("\nTroubleshooting:")
        print("  1. Ensure Gateway is running:")
        print("     lsof -i:9998")
        print("  2. Check Gateway logs:")
        print("     tail -f /tmp/logs/universal-llm-gateway/*.log")
        return 1

    if not with_model:
        print(f"\n⚠️  Model '{model_id}' not loaded on any reachable gateway")
        print(f"\nReachable gateways: {len(reachable)}")
        print("\nModel may need to be loaded first or ID may be incorrect.")
        return 1

    num_gateways_with_model = len(with_model)

    print(
        f"\nGateways with model: {num_gateways_with_model}/{len(reachable)} reachable"
    )

    for result in with_model:
        print(f"  • {result['gateway']}")

    if num_gateways_with_model == 1:
        print("\n✅ GOOD: Model loaded on SINGLE gateway")
        print("\nThis is correct for sticky models (default behavior).")
        print("Batching can work effectively with all requests on one gateway.")
        return 0
    else:
        print("\n❌ STICKY ROUTING VIOLATION")
        print(f"\nModel loaded on {num_gateways_with_model} gateways simultaneously.")
        print("For sticky models, this prevents effective batching:")
        print("  • Requests distributed across multiple gateways")
        print("  • Each gateway processes only subset of requests")
        print("  • Parallel slots cannot batch requests from different gateways")

        print("\n🔧 HOW TO FIX:")
        print("\n1. Stop all services:")
        print("   pkill -f 'universal-'")
        print("   rm -f /tmp/universal-protocol/*.sock /tmp/process_ipc/*.sock")

        print("\n2. Unload model from all but ONE gateway:")
        for i, result in enumerate(with_model):
            if i == 0:
                print(f"   Keep on: {result['gateway']}")
            else:
                print(f"   Unload from: {result['gateway']}")
                print(f"     curl -X POST {result['url']}/api/v1/models/unload \\")
                print("       -H 'Content-Type: application/json' \\")
                print(f'       -d \'{{"model_id": "{model_id}"}}\'')

        print("\n3. Verify configuration:")
        print("   • model_routing.default_sticky: true (should be set)")
        print(f"   • No sticky_overrides for '{model_id}'")

        print("\n4. For federated setups:")
        print("   • Use affinity rules to pin model to specific gateway")
        print("   • Or ensure only one gateway has VRAM capacity for model")

        print("\n5. Restart services and verify:")
        print(f"   ./scripts/check_gateway_model_distribution.py {model_id}")

        return 1


if __name__ == "__main__":
    sys.exit(main())
