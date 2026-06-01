#!/usr/bin/env python3
"""
Simple UDP listener for routing metrics emitted by Stargate.

This script listens on UDP port 10001 (default) and prints routing metrics
in real-time. Useful for debugging, monitoring, and understanding request flow.

Usage:
    python monitoring/routing_metrics_listener.py
    python monitoring/routing_metrics_listener.py --port 10002
    python monitoring/routing_metrics_listener.py --json  # Raw JSON output
"""

import argparse
import json
import socket
from datetime import datetime


def format_metric(metric):
    """Format metric for human-readable output"""
    metric_type = metric.get("type", "unknown")
    data = metric.get("data", {})
    timestamp = datetime.fromtimestamp(data.get("timestamp", 0)).strftime("%H:%M:%S.%")[
        :-3
    ]

    if metric_type == "request_routed":
        return (
            f"[{timestamp}] 🔀 REQUEST ROUTED\n"
            f"  Request: {data.get('request_id', 'unknown')}\n"
            f"  Model: {data.get('model_id', 'unknown')}\n"
            f"  Gateway: {data.get('gateway_name', 'unknown')}"
            f"({data.get('gateway_url', 'unknown')})\n"
            f"  Routing Time: {data.get('routing_time_ms', 0):.2f}ms\n"
            f"  Immediate: {data.get('immediate_route', False)}"
        )

    elif metric_type == "model_load_initiated":
        return (
            f"[{timestamp}] ⏳ MODEL LOAD INITIATED\n"
            f"  Model: {data.get('model_id', 'unknown')}\n"
            f"  Gateway: {data.get('gateway_name', 'unknown')}"
            f"({data.get('gateway_url', 'unknown')})\n"
            f"  Already Loaded: {data.get('already_loaded', False)}"
        )

    elif metric_type == "model_load_completed":
        success = "✅" if data.get("success", False) else "❌"
        result = (
            f"[{timestamp}] {success} MODEL LOAD COMPLETED\n"
            f"  Model: {data.get('model_id', 'unknown')}\n"
            f"  Gateway: {data.get('gateway_name', 'unknown')}"
            f"({data.get('gateway_url', 'unknown')})\n"
            f"  Load Time: {data.get('load_time_ms', 0):.2f}ms\n"
            f"  Success: {data.get('success', False)}"
        )
        if data.get("error"):
            result += f"\n  Error: {data.get('error')}"
        return result

    elif metric_type == "token_count_completed":
        success = "✅" if data.get("success", False) else "❌"
        result = (
            f"[{timestamp}] {success} TOKEN COUNT COMPLETED\n"
            f"  Model: {data.get('model_id', 'unknown')}\n"
            f"  Gateway: {data.get('gateway_url', 'unknown')}\n"
            f"  Count Time: {data.get('count_time_ms', 0):.2f}ms\n"
            f"  Input Tokens: {data.get('input_tokens', 0)}\n"
            f"  Context Limit: {data.get('context_limit', 0)}\n"
            f"  Allocated Max Tokens: {data.get('allocated_max_tokens', 0)}"
        )
        if data.get("error"):
            result += f"\n  Error: {data.get('error')}"
        return result

    else:
        payload = json.dumps(data, indent=2)
        return f"[{timestamp}] ❓ UNKNOWN METRIC: {metric_type}\n  Data: {payload}"


def main():
    parser = argparse.ArgumentParser(
        description="Listen for routing metrics from Universal Stargate"
    )
    parser.add_argument(
        "--port", type=int, default=10001, help="UDP port to listen on (default: 10001)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output raw JSON instead of formatted text"
    )
    parser.add_argument(
        "--filter",
        type=str,
        choices=[
            "request_routed",
            "model_load_initiated",
            "model_load_completed",
            "token_count_completed",
        ],
        help="Filter to specific metric type",
    )

    args = parser.parse_args()

    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))

    print(f"🎧 Listening for routing metrics on {args.host}:{args.port}")
    if args.filter:
        print(f"   Filtering: {args.filter}")
    print(f"   Format: {'JSON' if args.json else 'Formatted'}")
    print("=" * 80)

    try:
        while True:
            data, addr = sock.recvfrom(65535)  # Large buffer for metrics

            try:
                metric = json.loads(data.decode("utf-8"))

                # Apply filter if specified
                if args.filter and metric.get("type") != args.filter:
                    continue

                if args.json:
                    # Raw JSON output
                    print(json.dumps(metric, indent=2))
                else:
                    # Formatted output
                    print(format_metric(metric))
                    print()  # Blank line between metrics

            except json.JSONDecodeError as e:
                print(f"⚠️  Invalid JSON received: {e}")
            except Exception as e:
                print(f"⚠️  Error processing metric: {e}")

    except KeyboardInterrupt:
        print("\n\n👋 Shutting down listener...")
        sock.close()
        print("✅ Done!")


if __name__ == "__main__":
    main()
