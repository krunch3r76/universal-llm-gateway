#!/usr/bin/env python3
"""
Test script to verify batching behavior for parallel slot configuration.

Sends multiple concurrent requests to a model configured with parallel slots
and analyzes timing patterns to determine if requests are being batched
(processed in parallel) or queued sequentially.

Also checks for sticky routing violations that would prevent effective batching.
"""

import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import requests


@dataclass(slots=True, kw_only=True)
class RequestResult:
    """Result of a single request with timing information."""

    request_id: int
    start_time: float
    end_time: float
    duration: float
    success: bool
    error: str | None = None
    response_length: int = 0
    gateway_id: str | None = None  # Which gateway processed this request


def send_request(
    model_id: str,
    request_id: int,
    base_url: str,
) -> RequestResult:
    """
    Send a single chat completion request and track timing.

    Args:
        model_id: Model identifier to test
        request_id: Unique ID for this request
        base_url: API endpoint base URL

    Returns:
        RequestResult with timing and success information
    """
    payload = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": f"Request {request_id}: Write a detailed explanation of the Pythagorean theorem, including its history, proof, and practical applications. Be thorough and comprehensive.",
            }
        ],
        "temperature": 0.7,
        "max_tokens": 800,
    }

    start_time = time.perf_counter()

    try:
        response = requests.post(
            f"{base_url}/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=120.0,
        )
        response.raise_for_status()

        end_time = time.perf_counter()
        duration = end_time - start_time

        result_data = response.json()
        content = result_data["choices"][0]["message"]["content"]

        # Extract gateway ID if present in response headers or body
        gateway_id = (
            response.headers.get("X-Gateway-ID")
            or response.headers.get("X-Worker-ID")
            or result_data.get("_gateway_id")
            or result_data.get("system_fingerprint")
        )

        return RequestResult(
            request_id=request_id,
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            success=True,
            response_length=len(content),
            gateway_id=gateway_id,
        )

    except Exception as e:
        end_time = time.perf_counter()
        duration = end_time - start_time

        return RequestResult(
            request_id=request_id,
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            success=False,
            error=str(e),
        )


def run_concurrent_requests(
    model_id: str,
    num_requests: int,
    base_url: str,
) -> list[RequestResult]:
    """
    Send multiple concurrent requests and collect results.

    Args:
        model_id: Model identifier to test
        num_requests: Number of concurrent requests to send
        base_url: API endpoint base URL

    Returns:
        List of RequestResult objects with timing data
    """
    results: list[RequestResult] = []

    with ThreadPoolExecutor(max_workers=num_requests) as executor:
        futures = {
            executor.submit(send_request, model_id, i, base_url): i
            for i in range(1, num_requests + 1)
        }

        for future in as_completed(futures):
            result = future.result()
            results.append(result)

    return results


def run_sequential_requests(
    model_id: str,
    num_requests: int,
    base_url: str,
) -> list[RequestResult]:
    """
    Send multiple requests sequentially (one at a time) for baseline.

    Args:
        model_id: Model identifier to test
        num_requests: Number of sequential requests to send
        base_url: API endpoint base URL

    Returns:
        List of RequestResult objects with timing data
    """
    results: list[RequestResult] = []

    for i in range(1, num_requests + 1):
        result = send_request(model_id, i, base_url)
        results.append(result)

    return results


def calculate_wall_time(results: list[RequestResult]) -> float:
    """Calculate total wall clock time from start to finish."""
    if not results:
        return 0.0

    sorted_results = sorted(results, key=lambda r: r.start_time)
    base_time = sorted_results[0].start_time
    max_end = max(r.end_time for r in sorted_results)

    return max_end - base_time


def check_sticky_routing_violation(
    results: list[RequestResult],
) -> dict[str, int | bool]:
    """
    Check if requests were distributed across multiple gateways.

    For sticky models, all requests should go to the same gateway.
    Distribution across multiple gateways prevents effective batching.

    Args:
        results: List of request results with gateway information

    Returns:
        Dict with gateway distribution and violation status
    """
    successful_with_gateway = [
        r for r in results if r.success and r.gateway_id is not None
    ]

    if not successful_with_gateway:
        return {
            "violation_detected": False,
            "reason": "No gateway information available in responses",
            "num_gateways": 0,
            "distribution": {},
        }

    gateway_counts = Counter(r.gateway_id for r in successful_with_gateway)
    num_gateways = len(gateway_counts)

    return {
        "violation_detected": num_gateways > 1,
        "num_gateways": num_gateways,
        "distribution": dict(gateway_counts),
        "reason": (
            f"Requests distributed across {num_gateways} gateways"
            if num_gateways > 1
            else "All requests on single gateway"
        ),
    }


def analyze_batching_behavior(
    sequential_results: list[RequestResult],
    concurrent_results: list[RequestResult],
    parallel_slots: int,
) -> dict[str, bool | int | float | str]:
    """
    Analyze timing patterns by comparing sequential vs concurrent execution.

    Args:
        sequential_results: Results from sequential execution (baseline)
        concurrent_results: Results from concurrent execution
        parallel_slots: Expected number of parallel slots

    Returns:
        Analysis summary with batching verdict
    """
    seq_successful = [r for r in sequential_results if r.success]
    conc_successful = [r for r in concurrent_results if r.success]

    if not seq_successful or not conc_successful:
        return {
            "batching_detected": False,
            "error": "Missing successful requests in baseline or concurrent test",
        }

    # Sequential baseline metrics
    seq_wall_time = calculate_wall_time(seq_successful)
    seq_avg_duration = sum(r.duration for r in seq_successful) / len(seq_successful)

    # Concurrent execution metrics
    conc_wall_time = calculate_wall_time(conc_successful)
    conc_avg_duration = sum(r.duration for r in conc_successful) / len(conc_successful)

    # Calculate concurrent request overlap
    sorted_results = sorted(conc_successful, key=lambda r: r.start_time)
    base_time = sorted_results[0].start_time

    time_points: list[tuple[float, int]] = []
    for result in sorted_results:
        time_points.append((result.start_time - base_time, 1))
        time_points.append((result.end_time - base_time, -1))

    time_points.sort()

    max_concurrent = 0
    current_concurrent = 0
    for _, delta in time_points:
        current_concurrent += delta
        max_concurrent = max(max_concurrent, current_concurrent)

    # Calculate speedup and efficiency
    speedup = seq_wall_time / conc_wall_time if conc_wall_time > 0 else 0
    theoretical_max_speedup = min(parallel_slots, len(conc_successful))
    efficiency_percent = (
        (speedup / theoretical_max_speedup * 100) if theoretical_max_speedup > 0 else 0
    )

    # Batching verdict: speedup should be close to number of slots (within reason)
    # Consider batching working if speedup >= 60% of theoretical max
    batching_detected = speedup >= (theoretical_max_speedup * 0.6)

    return {
        "batching_detected": batching_detected,
        "sequential_wall_time_seconds": round(seq_wall_time, 3),
        "concurrent_wall_time_seconds": round(conc_wall_time, 3),
        "sequential_avg_duration_seconds": round(seq_avg_duration, 3),
        "concurrent_avg_duration_seconds": round(conc_avg_duration, 3),
        "speedup": round(speedup, 2),
        "theoretical_max_speedup": theoretical_max_speedup,
        "efficiency_percent": round(efficiency_percent, 1),
        "max_concurrent_requests": max_concurrent,
        "expected_parallel_slots": parallel_slots,
        "sequential_successful": len(seq_successful),
        "sequential_failed": len(sequential_results) - len(seq_successful),
        "concurrent_successful": len(conc_successful),
        "concurrent_failed": len(concurrent_results) - len(conc_successful),
    }


def print_detailed_timing(results: list[RequestResult]) -> None:
    """Print detailed timing information for each request."""
    print("\n" + "=" * 80)
    print("DETAILED REQUEST TIMING")
    print("=" * 80)

    successful_results = [r for r in results if r.success]
    if not successful_results:
        print("No successful requests to display")
        return

    # Sort by start time and normalize
    sorted_results = sorted(successful_results, key=lambda r: r.start_time)
    base_time = sorted_results[0].start_time

    print(
        f"\n{'ID':<5} {'Start (s)':<12} {'End (s)':<12} {'Duration (s)':<15} {'Status'}"
    )
    print("-" * 80)

    for result in sorted_results:
        start_rel = result.start_time - base_time
        end_rel = result.end_time - base_time
        status = "✓ Success" if result.success else f"✗ {result.error}"

        print(
            f"{result.request_id:<5} "
            f"{start_rel:<12.3f} "
            f"{end_rel:<12.3f} "
            f"{result.duration:<15.3f} "
            f"{status}"
        )

    # Show timeline visualization
    print("\n" + "=" * 80)
    print("CONCURRENT EXECUTION TIMELINE")
    print("=" * 80)
    print("\nEach '█' represents a request in progress (0.5s intervals)")
    print()

    max_time = max(r.end_time - base_time for r in sorted_results)
    time_scale = 0.5  # seconds per character

    for result in sorted_results:
        start_rel = result.start_time - base_time
        end_rel = result.end_time - base_time

        start_pos = int(start_rel / time_scale)
        duration_chars = max(1, int(result.duration / time_scale))

        timeline = " " * start_pos + "█" * duration_chars
        print(f"Req {result.request_id:2d}: {timeline}")

    # Time axis
    num_marks = int(max_time / time_scale) + 1
    time_axis = "".join(
        str(int(i * time_scale)) if i % 2 == 0 else " " for i in range(num_marks)
    )
    print(f"\nTime:   {time_axis}")
    print("(seconds from start)")


def main() -> int:
    """Run batching behavior test and report results."""
    model_id = "phi-3-5-mini-instruct-q8-0-16384"
    parallel_slots = 8
    num_requests = 12  # Send more than slot count to test batching
    base_url = "http://localhost:9999"

    print("=" * 80)
    print("BATCHING BEHAVIOR TEST")
    print("=" * 80)
    print(f"\nModel: {model_id}")
    print(f"Expected parallel slots: {parallel_slots}")
    print(f"Number of requests: {num_requests}")
    print(f"API endpoint: {base_url}")

    # Phase 1: Sequential baseline
    print("\n" + "=" * 80)
    print("PHASE 1: SEQUENTIAL BASELINE")
    print("=" * 80)
    print(f"\nSending {num_requests} requests sequentially (one at a time)...")
    print("This establishes the baseline for comparison.")
    print()

    try:
        sequential_results = run_sequential_requests(model_id, num_requests, base_url)
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        return 130
    except Exception as e:
        print(f"\n❌ Sequential test failed with error: {e}")
        return 1

    seq_successful = [r for r in sequential_results if r.success]
    seq_wall_time = calculate_wall_time(seq_successful) if seq_successful else 0

    print(
        f"✓ Sequential baseline complete: {len(seq_successful)}/{num_requests} succeeded"
    )
    print(f"  Total wall time: {seq_wall_time:.3f}s")

    # Phase 2: Concurrent execution
    print("\n" + "=" * 80)
    print("PHASE 2: CONCURRENT EXECUTION")
    print("=" * 80)
    print(f"\nSending {num_requests} requests concurrently...")
    print("Testing if parallel slots enable true batching.")
    print()

    try:
        concurrent_results = run_concurrent_requests(model_id, num_requests, base_url)
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        return 130
    except Exception as e:
        print(f"\n❌ Concurrent test failed with error: {e}")
        return 1

    conc_successful = [r for r in concurrent_results if r.success]
    conc_wall_time = calculate_wall_time(conc_successful) if conc_successful else 0

    print(
        f"✓ Concurrent execution complete: {len(conc_successful)}/{num_requests} succeeded"
    )
    print(f"  Total wall time: {conc_wall_time:.3f}s")

    print_detailed_timing(concurrent_results)

    # Check for sticky routing violation
    print("\n" + "=" * 80)
    print("STICKY ROUTING CHECK")
    print("=" * 80)

    sticky_check = check_sticky_routing_violation(concurrent_results)

    if sticky_check["num_gateways"] == 0:
        print(f"\n⚠️  {sticky_check['reason']}")
        print("\nNote: Cannot detect gateway distribution without response metadata.")
        print("This is expected if gateway_id is not included in responses/headers.")
    elif sticky_check["violation_detected"]:
        print("\n❌ STICKY ROUTING VIOLATION DETECTED")
        print(f"\n{sticky_check['reason']}")
        print("\nGateway distribution:")
        for gateway_id, count in sticky_check["distribution"].items():
            print(f"  {gateway_id}: {count} requests")

        print("\n⚠️  For sticky models, all requests should go to ONE gateway.")
        print("Distribution across multiple gateways prevents effective batching:")
        print("  • Each gateway processes only a subset of requests")
        print("  • Parallel slots cannot batch requests from different gateways")
        print("  • Results in sequential or limited parallel processing")

        print("\n🔍 Diagnosis:")
        print(
            f"  1. Check if model is loaded on {sticky_check['num_gateways']} gateways"
        )
        print("  2. Verify model_routing.default_sticky: true in config")
        print(f"  3. Ensure no sticky_overrides for model '{model_id}'")
        print("  4. If multiple instances, unload from all but one gateway")
    else:
        print(
            f"\n✅ Sticky routing appears correct (single gateway: {list(sticky_check['distribution'].keys())[0]})"
        )

    analysis = analyze_batching_behavior(
        sequential_results, concurrent_results, parallel_slots
    )

    print("\n" + "=" * 80)
    print("BATCHING ANALYSIS")
    print("=" * 80)

    if "error" in analysis:
        print(f"\n❌ Analysis failed: {analysis['error']}")
        return 1

    print("\n📊 BASELINE (Sequential):")
    print(
        f"  Successful requests: {analysis['sequential_successful']}/{analysis['sequential_successful'] + analysis['sequential_failed']}"
    )
    print(f"  Total wall time: {analysis['sequential_wall_time_seconds']}s")
    print(f"  Average per request: {analysis['sequential_avg_duration_seconds']}s")

    print("\n📊 TEST (Concurrent):")
    print(
        f"  Successful requests: {analysis['concurrent_successful']}/{analysis['concurrent_successful'] + analysis['concurrent_failed']}"
    )
    print(f"  Total wall time: {analysis['concurrent_wall_time_seconds']}s")
    print(f"  Average per request: {analysis['concurrent_avg_duration_seconds']}s")
    print(f"  Max concurrent observed: {analysis['max_concurrent_requests']}")

    print("\n📈 PERFORMANCE:")
    print(f"  Speedup: {analysis['speedup']}x")
    print(f"  Theoretical max speedup: {analysis['theoretical_max_speedup']}x")
    print(f"  Efficiency: {analysis['efficiency_percent']}%")
    print(f"  Expected parallel slots: {analysis['expected_parallel_slots']}")

    print("\n" + "=" * 80)
    print("VERDICT")
    print("=" * 80)

    if analysis["batching_detected"]:
        print("\n✅ BATCHING IS WORKING")
        print(
            f"\nConcurrent execution achieved {analysis['speedup']}x speedup over sequential."
        )
        print(
            f"Observed {analysis['max_concurrent_requests']} concurrent requests (expected: {parallel_slots})."
        )
        print(f"Efficiency: {analysis['efficiency_percent']}% of theoretical maximum")

        if analysis["speedup"] >= analysis["theoretical_max_speedup"] * 0.9:
            print("\n🎯 Performance is EXCELLENT - near-optimal batching!")
        elif analysis["speedup"] >= analysis["theoretical_max_speedup"] * 0.75:
            print("\n✓ Performance is GOOD - effective batching with minor overhead")
        else:
            print(
                "\n⚠️  Performance is ACCEPTABLE - batching works but has some inefficiency"
            )

        return 0
    else:
        print("\n❌ BATCHING IS NOT WORKING")
        print(
            f"\nConcurrent execution only achieved {analysis['speedup']}x speedup (expected: ~{analysis['theoretical_max_speedup']}x)."
        )
        print(
            f"Observed {analysis['max_concurrent_requests']} max concurrent (expected: {parallel_slots})."
        )
        print(f"Efficiency: {analysis['efficiency_percent']}% (expected: >60%)")

        print("\n🔍 Possible causes:")
        if sticky_check["violation_detected"]:
            print(
                f"  • [LIKELY CAUSE] Sticky routing violation: {sticky_check['num_gateways']} gateways"
            )
            print("    → Requests distributed across gateways cannot batch together")
        print("  • Model not loaded with parallel_slots configuration")
        print("  • Requests being queued instead of batched")
        print("  • Slot allocation logic not working correctly")
        print("  • Model worker not initialized with batching support")
        print("  • Context size limiting concurrent batch processing")

        return 1


if __name__ == "__main__":
    sys.exit(main())
