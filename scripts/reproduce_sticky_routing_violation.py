#!/usr/bin/env python3
"""
Reproduce sticky routing violation: concurrent requests to a sticky model
can cause the model to be loaded on two edges/gateways instead of one.

Sends several small concurrent requests to Stargate for a given model
(default: phi-3-5-mini-instruct-q8-0-16384), then checks how many
gateways have the model loaded. For sticky (default) routing, exactly
one gateway should have the model.

Usage:
  source ~/.venvs/universal/bin/activate
  python scripts/reproduce_sticky_routing_violation.py [MODEL_ID] [--stargate URL] [--concurrent N] [--gateways URL ...] [--debug]
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import requests

# Default gateway URLs to probe if not provided (relay/local)
DEFAULT_GATEWAY_PORTS = (9998, 9997, 9996, 9995)


@dataclass(slots=True, kw_only=True)
class RequestResult:
    """Result of one chat completion request."""

    request_id: int
    success: bool
    duration: float
    error: str | None = None
    gateway_id: str | None = None  # From response header or body


def get_gateway_loaded_models(
    gateway_url: str, timeout: float = 5.0
) -> dict[str, list[str] | str]:
    """
    Query Gateway API for loaded models.

    Returns:
        Dict with 'models' list or 'error' string.
    """
    try:
        response = requests.get(
            f"{gateway_url}/api/v1/status/detailed",
            timeout=timeout,
        )
        if response.status_code == 200:
            data = response.json()
            loaded: list[str] = []
            if "loaded_models" in data:
                loaded = list(data["loaded_models"])
            elif "models" in data and isinstance(data["models"], dict):
                loaded = [
                    mid
                    for mid, info in data["models"].items()
                    if isinstance(info, dict) and info.get("status") == "loaded"
                ]
            return {"models": loaded}
        response = requests.get(f"{gateway_url}/health", timeout=timeout)
        if response.status_code == 200:
            loaded = list(response.json().get("loaded_models", []))
            return {"models": loaded}
        return {"error": f"HTTP {response.status_code}"}
    except requests.exceptions.Timeout:
        return {"error": "timeout"}
    except requests.exceptions.ConnectionError:
        return {"error": "connection_refused"}
    except Exception as e:
        return {"error": str(e)}


def discover_gateway_urls(base_host: str = "http://localhost") -> list[str]:
    """Probe common ports; return URLs that respond to /health."""
    urls: list[str] = []
    for port in DEFAULT_GATEWAY_PORTS:
        url = f"{base_host}:{port}"
        try:
            r = requests.get(f"{url}/health", timeout=2.0)
            if r.status_code == 200:
                urls.append(url)
        except Exception:
            pass
    return urls


def send_one_request(
    stargate_url: str,
    model_id: str,
    request_id: int,
    timeout: float = 60.0,
) -> RequestResult:
    """Send a single small chat completion request and return result."""
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": f"Say {request_id}."}],
        "max_tokens": 5,
        "stream": False,
    }
    start = time.perf_counter()
    try:
        response = requests.post(
            f"{stargate_url}/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        duration = time.perf_counter() - start
        data = response.json()
        gateway_id: str | None = (
            response.headers.get("x-federation-gateway")
            or response.headers.get("X-Gateway-ID")
            or (data.get("_gateway_id") if isinstance(data, dict) else None)
        )
        return RequestResult(
            request_id=request_id,
            success=True,
            duration=duration,
            gateway_id=gateway_id,
        )
    except Exception as e:
        duration = time.perf_counter() - start
        return RequestResult(
            request_id=request_id,
            success=False,
            duration=duration,
            error=str(e),
        )


def run_concurrent_requests(
    stargate_url: str,
    model_id: str,
    num_requests: int,
    timeout: float = 60.0,
) -> list[RequestResult]:
    """Send num_requests concurrent requests and return results."""
    results: list[RequestResult] = []
    with ThreadPoolExecutor(max_workers=num_requests) as executor:
        futures = {
            executor.submit(send_one_request, stargate_url, model_id, i, timeout): i
            for i in range(1, num_requests + 1)
        }
        for future in as_completed(futures):
            results.append(future.result())
    return results


def gateways_with_model(
    gateway_urls: list[str], model_id: str, timeout: float = 5.0
) -> list[str]:
    """Return list of gateway URLs that have model_id loaded."""
    out: list[str] = []
    for url in gateway_urls:
        result = get_gateway_loaded_models(url, timeout=timeout)
        if "models" in result and model_id in result["models"]:
            out.append(url)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reproduce sticky routing violation via concurrent requests."
    )
    parser.add_argument(
        "model_id",
        nargs="?",
        default="phi-3-5-mini-instruct-q8-0-16384",
        help="Model ID (default: phi-3-5-mini-instruct-q8-0-16384)",
    )
    parser.add_argument(
        "--stargate",
        default="http://localhost:9999",
        help="Stargate base URL (default: http://localhost:9999)",
    )
    parser.add_argument(
        "--concurrent",
        type=int,
        default=6,
        help="Number of concurrent requests (default: 6)",
    )
    parser.add_argument(
        "--gateways",
        nargs="*",
        default=None,
        help="Gateway URLs to check for model load. If omitted, probe localhost:9998,9997,9996,9995",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Write summary to /tmp/sticky_repro_debug.txt",
    )
    args = parser.parse_args()

    model_id = args.model_id
    stargate_url = args.stargate.rstrip("/")
    num_requests = args.concurrent
    gateway_urls = args.gateways
    if not gateway_urls:
        gateway_urls = discover_gateway_urls()
    if not gateway_urls:
        print(
            "No gateways to check. Specify --gateways or ensure local gateways on 9998-9995."
        )
        return 1

    print("=" * 60)
    print("STICKY ROUTING VIOLATION REPRODUCTION")
    print("=" * 60)
    print(f"Model: {model_id}")
    print(f"Stargate: {stargate_url}")
    print(f"Concurrent requests: {num_requests}")
    print(f"Gateways to check: {gateway_urls}")
    print()

    print("Sending concurrent requests...")
    results = run_concurrent_requests(stargate_url, model_id, num_requests)
    success_count = sum(1 for r in results if r.success)
    failed = [r for r in results if not r.success]
    if failed:
        for r in failed:
            print(f"  Request {r.request_id} failed: {r.error}")
    print(f"Completed: {success_count}/{num_requests} successful.")
    print()

    # Distinct gateways from response headers (if any)
    response_gateways = list(
        {r.gateway_id for r in results if r.success and r.gateway_id}
    )
    if response_gateways:
        print(f"Gateways seen in responses: {response_gateways}")
        if len(response_gateways) > 1:
            print(
                "  -> Multiple gateways served requests (sticky violation by response)."
            )
    else:
        print("(No gateway IDs in responses; checking loaded models on each gateway.)")
    print()

    # How many gateways have the model loaded (probed via API)
    with_model = gateways_with_model(gateway_urls, model_id)
    print(f"Gateways with model loaded (probed): {len(with_model)}")
    for url in with_model:
        print(f"  - {url}")
    print()

    # Detect violation from EITHER probed gateways OR response headers.
    # In federated setups, individual gateways may not be directly reachable
    # for probing, so response headers are the authoritative signal.
    probe_violation = len(with_model) > 1
    header_violation = len(response_gateways) > 1
    violation = probe_violation or header_violation

    if violation:
        print("STICKY ROUTING VIOLATION")
        if header_violation:
            print(
                f"  Multiple gateways served requests (response headers): "
                f"{response_gateways}"
            )
        if probe_violation:
            print(
                f"  Model loaded on {len(with_model)} gateways (probed): {with_model}"
            )
        print(
            "For sticky (default) routing, exactly one gateway should hold the model."
        )
        exit_code = 1
    else:
        print("OK: Model loaded on at most one gateway.")
        exit_code = 0

    if args.debug:
        debug_path = "/tmp/sticky_repro_debug.txt"
        with open(debug_path, "w") as f:
            f.write(f"model_id={model_id}\n")
            f.write(f"stargate={stargate_url}\n")
            f.write(f"concurrent={num_requests}\n")
            f.write(f"success={success_count}\n")
            f.write(f"response_gateways={response_gateways}\n")
            f.write(f"gateways_with_model={with_model}\n")
            f.write(f"probe_violation={probe_violation}\n")
            f.write(f"header_violation={header_violation}\n")
            f.write(f"violation={violation}\n")
        print(f"Debug summary written to {debug_path}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
