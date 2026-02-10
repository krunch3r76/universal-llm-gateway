#!/usr/bin/env python3
"""
Debug script to verify activation filtering in /v1/models.

Fetches model lists from a running Stargate and shows:
- Available models (full catalog for routing)
- Activated models (filtered for /v1/models display)
- Activation rules applied

Usage:
    python scripts/dev/check_activation_filtering.py [--url URL] [--output FILE]

Example:
    python scripts/dev/check_activation_filtering.py --url http://localhost:9999
    python scripts/dev/check_activation_filtering.py --output /tmp/activation_debug.log
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import httpx


def fetch_models(base_url: str) -> list[dict]:
    """Fetch model list from /v1/models endpoint."""
    url = f"{base_url}/v1/models"
    response = httpx.get(url, timeout=30.0)
    response.raise_for_status()
    data = response.json()
    return data.get("data", [])


def fetch_gateway_status(base_url: str) -> dict | None:
    """Fetch federated gateway status (internal endpoint)."""
    url = f"{base_url}/api/v1/federation/status"
    try:
        response = httpx.get(url, timeout=30.0)
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def group_models_by_base(models: list[dict]) -> dict[str, list[str]]:
    """Group model IDs by their base model name."""
    groups: dict[str, list[str]] = {}
    for model in models:
        model_id = model.get("id", "")
        # Extract base model (before context suffix)
        parts = model_id.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            # Has context suffix
            base = parts[0]
            # Check for hybrid/cpu suffix
            if base.endswith("-hybrid"):
                base = base[:-7]
            elif base.endswith("-cpu"):
                base = base[:-4]
            groups.setdefault(base, []).append(model_id)
        else:
            # No context suffix
            groups.setdefault(model_id, []).append(model_id)
    return groups


def main() -> int:
    parser = argparse.ArgumentParser(description="Check activation filtering")
    parser.add_argument(
        "--url",
        default="http://localhost:9999",
        help="Stargate base URL (default: http://localhost:9999)",
    )
    parser.add_argument(
        "--output",
        help="Output file path (default: stdout)",
    )
    args = parser.parse_args()

    lines: list[str] = []

    def log(msg: str) -> None:
        lines.append(msg)
        if not args.output:
            print(msg)

    log("=== Activation Filtering Debug ===")
    log(f"Timestamp: {datetime.now().isoformat()}")
    log(f"Stargate URL: {args.url}")
    log("")

    # Fetch /v1/models
    try:
        models = fetch_models(args.url)
        log(f"✅ /v1/models returned {len(models)} models")
    except Exception as e:
        log(f"❌ Failed to fetch /v1/models: {e}")
        return 1

    # Group by base model
    groups = group_models_by_base(models)
    log(f"   Base models: {len(groups)}")
    log("")

    # Show models by base
    log("=== Models by Base ===")
    for base, model_ids in sorted(groups.items()):
        log(f"  {base}:")
        for mid in sorted(model_ids):
            suffix = mid[len(base) :] if mid.startswith(base) else mid
            log(f"    - {suffix or '(base)'}")
    log("")

    # Check specific models mentioned in task
    log("=== Verification: Expected Activation ===")

    gemma_models = [m for m in models if "gemma-2-9b-it-q4-k-m" in m.get("id", "")]
    log("gemma-2-9b-it-q4-k-m (activated_gpu_contexts: [8192,4096,2048,1024]):")
    if gemma_models:
        for m in gemma_models:
            log(f"  ✅ {m.get('id')}")
    else:
        log("  ⚠️ Not found in /v1/models")

    codellama_models = [
        m for m in models if "codellama-34b-instruct-hf-q6-k" in m.get("id", "")
    ]
    log("codellama-34b-instruct-hf-q6-k (activated_gpu_contexts: []):")
    if codellama_models:
        for m in codellama_models:
            log(f"  ⚠️ {m.get('id')} - should not appear (empty activation list)")
    else:
        log("  ✅ Not in /v1/models (expected: empty list = no display)")

    log("")

    # Try to get federation status
    status = fetch_gateway_status(args.url)
    if status:
        log("=== Federation Gateway Status ===")
        gateways = status.get("gateways", {})
        for gw_id, gw_info in gateways.items():
            available = len(gw_info.get("models", []))
            log(
                f"  {gw_id}: {available} available models, connected={gw_info.get('is_connected')}"
            )
    else:
        log(
            "(Federation status not available - may be non-federated or endpoint not exposed)"
        )

    log("")
    log("=== Summary ===")
    log(f"Total models in /v1/models: {len(models)}")
    log(f"Unique base models: {len(groups)}")

    # Write to file if specified
    if args.output:
        with open(args.output, "w") as f:
            f.write("\n".join(lines))
        print(f"Output written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
