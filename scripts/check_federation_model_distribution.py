#!/usr/bin/env python3
"""Check model distribution across federated edges via Master Stargate."""

import sys
import json
from typing import Any
import requests


def get_federation_status(master_url: str = "http://localhost:9999") -> dict[str, Any] | None:
    """Query Master Stargate for federation status."""
    try:
        response = requests.get(
            f"{master_url}/api/v1/federation/status",
            timeout=5
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"  ❌ Failed to get federation status: {e}")
        return None


def check_model_on_edges(
    model_id: str,
    master_url: str = "http://localhost:9999",
    num_requests: int = 10
) -> dict[str, int]:
    """Send test requests and track which edges serve them."""
    edge_counts: dict[str, int] = {}
    
    print(f"\nSending {num_requests} test requests to track edge routing...")
    
    for i in range(num_requests):
        try:
            response = requests.post(
                f"{master_url}/v1/chat/completions",
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": "test"}],
                    "max_tokens": 1,
                    "stream": False
                },
                timeout=30
            )
            
            # Try to extract edge identifier from response
            edge_id = None
            
            # Check headers first
            for header in ["X-Edge-ID", "X-Gateway-ID", "X-Stargate-ID"]:
                if header in response.headers:
                    edge_id = response.headers[header]
                    break
            
            # If no header, try response body
            if not edge_id:
                try:
                    data = response.json()
                    edge_id = data.get("system_fingerprint") or data.get("_edge_id")
                except Exception:
                    pass
            
            if edge_id:
                edge_counts[edge_id] = edge_counts.get(edge_id, 0) + 1
                print(f"  Request {i+1}: {edge_id}")
            else:
                print(f"  Request {i+1}: (edge ID not available)")
                
        except requests.exceptions.RequestException as e:
            print(f"  Request {i+1}: Failed - {e}")
    
    return edge_counts


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: check_federation_model_distribution.py <model_id>")
        print("\nExample:")
        print("  check_federation_model_distribution.py phi-3-5-mini-instruct-q8-0-16384")
        sys.exit(1)
    
    model_id = sys.argv[1]
    master_url = "http://localhost:9999"
    
    print("=" * 80)
    print("FEDERATION MODEL DISTRIBUTION CHECK")
    print("=" * 80)
    print(f"\nModel: {model_id}")
    print(f"Master: {master_url}")
    
    # Step 1: Get federation status
    print("\n--- Federation Status ---")
    fed_status = get_federation_status(master_url)
    
    if fed_status:
        edges = fed_status.get("edges", [])
        print(f"  Edges registered: {len(edges)}")
        for edge in edges:
            edge_id = edge.get("edge_id", "unknown")
            status = edge.get("status", "unknown")
            print(f"    - {edge_id}: {status}")
    
    # Step 2: Track routing via test requests
    print("\n--- Model Routing Test ---")
    edge_counts = check_model_on_edges(model_id, master_url, num_requests=10)
    
    # Analysis
    print("\n" + "=" * 80)
    print("ANALYSIS")
    print("=" * 80)
    
    if not edge_counts:
        print("\n❌ Could not determine edge routing")
        print("   (Edge identifiers not available in responses)")
    elif len(edge_counts) == 1:
        edge_id = list(edge_counts.keys())[0]
        print(f"\n✅ Sticky routing CORRECT")
        print(f"   All requests routed to: {edge_id}")
    else:
        print(f"\n❌ STICKY ROUTING VIOLATION DETECTED")
        print(f"   Model is loaded on {len(edge_counts)} different edges:")
        for edge_id, count in sorted(edge_counts.items()):
            print(f"     - {edge_id}: {count} requests")
        
        print("\n   This breaks batching! Concurrent requests are distributed")
        print("   across multiple gateways instead of batching on one.")
        
        print("\n   Solutions:")
        print("   1. Manually unload from one edge")
        print("   2. Add affinity rule in stargate config")
        print("   3. Adjust capacity constraints to prevent multi-loading")
        print("   4. Clean restart: stop all → clean sockets → start all")


if __name__ == "__main__":
    main()
