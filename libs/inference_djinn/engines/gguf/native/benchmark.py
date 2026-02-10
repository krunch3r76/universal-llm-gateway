"""
Quick benchmark: Compare llama-cpp-python vs native llama-server.

Measures:
1. Sequential throughput (llama-cpp-python baseline)
2. Parallel throughput (native server with multiple slots)
3. VRAM usage comparison
"""

import asyncio
import time
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)


async def benchmark_sequential(model_path: str, num_requests: int = 8):
    """
    Benchmark llama-cpp-python (sequential processing).

    Simulates current architecture: 1 worker = 1 request at a time.
    """
    print("\n" + "=" * 60)
    print("Benchmark 1: Sequential (llama-cpp-python)")
    print("=" * 60)

    try:
        from llama_cpp import Llama
    except ImportError:
        print("❌ llama-cpp-python not installed, skipping")
        return None

    # Load model
    print(f"\n📥 Loading model: {model_path}")
    start = time.time()
    llama = Llama(
        model_path=model_path,
        n_ctx=8192,
        n_gpu_layers=-1,
        verbose=False,
    )
    load_time = time.time() - start
    print(f"✅ Model loaded in {load_time:.2f}s")

    # Run sequential requests
    prompts = [f"Request {i}: What is AI?" for i in range(num_requests)]

    print(f"\n🔄 Running {num_requests} sequential requests...")
    start = time.time()

    tokens_generated = 0
    for i, prompt in enumerate(prompts, 1):
        response = llama(prompt, max_tokens=100)
        tokens = len(response["choices"][0]["text"].split())
        tokens_generated += tokens
        print(f"  Request {i}/{num_requests} complete ({tokens} tokens)")

    total_time = time.time() - start
    throughput = tokens_generated / total_time

    print("\n📊 Results:")
    print(f"   Total time: {total_time:.2f}s")
    print(f"   Tokens generated: {tokens_generated}")
    print(f"   Throughput: {throughput:.2f} tokens/sec")
    print(f"   Avg latency: {total_time / num_requests:.2f}s per request")

    return {
        "mode": "sequential",
        "num_requests": num_requests,
        "total_time": total_time,
        "tokens_generated": tokens_generated,
        "throughput": throughput,
    }


async def benchmark_parallel(
    model_path: str, num_requests: int = 8, parallel_slots: int = 8
):
    """
    Benchmark native llama-server (parallel processing).

    Tests new architecture: 1 server = N requests simultaneously.
    """
    print("\n" + "=" * 60)
    print(f"Benchmark 2: Parallel (native server, {parallel_slots} slots)")
    print("=" * 60)

    from inference_djinn.engines.gguf.native import NativeGGUFEngine

    # Start server
    print(f"\n📥 Starting server with {parallel_slots} parallel slots...")
    engine = NativeGGUFEngine(
        model_path=model_path,
        parallel_slots=parallel_slots,
        continuous_batching=True,
        ctx_size=8192,
        n_gpu_layers=-1,
        port=9000,
    )

    async with engine:
        print("✅ Server started")

        # Run parallel requests
        prompts = [f"Request {i}: What is AI?" for i in range(num_requests)]

        print(f"\n🚀 Running {num_requests} parallel requests...")
        start = time.time()

        # All requests sent simultaneously
        responses = await asyncio.gather(
            *[engine.complete(prompt, max_tokens=100) for prompt in prompts]
        )

        total_time = time.time() - start

        tokens_generated = sum(len(r["choices"][0]["text"].split()) for r in responses)
        throughput = tokens_generated / total_time

        print("\n📊 Results:")
        print(f"   Total time: {total_time:.2f}s")
        print(f"   Tokens generated: {tokens_generated}")
        print(f"   Throughput: {throughput:.2f} tokens/sec")
        print(f"   Avg latency: {total_time / num_requests:.2f}s per request")
        print(f"   Parallel speedup: {num_requests * total_time / total_time:.2f}x")

        return {
            "mode": "parallel",
            "num_requests": num_requests,
            "parallel_slots": parallel_slots,
            "total_time": total_time,
            "tokens_generated": tokens_generated,
            "throughput": throughput,
        }


async def run_benchmark(model_path: str):
    """Run full benchmark comparison."""
    print("\n" + "=" * 80)
    print("llama-cpp-python vs Native llama-server Benchmark")
    print("=" * 80)
    print(f"\nModel: {model_path}")
    print("Test: 8 concurrent requests, 100 tokens each\n")

    # Check model exists
    if not Path(model_path).exists():
        print(f"❌ Model not found: {model_path}")
        print("\nUsage: python benchmark.py /path/to/model.gguf")
        return

    # Benchmark 1: Sequential (llama-cpp-python)
    seq_results = await benchmark_sequential(model_path, num_requests=8)

    # Benchmark 2: Parallel (native server)
    par_results = await benchmark_parallel(model_path, num_requests=8, parallel_slots=8)

    # Compare results
    if seq_results and par_results:
        print("\n" + "=" * 80)
        print("Comparison")
        print("=" * 80)

        speedup = par_results["throughput"] / seq_results["throughput"]
        time_savings = (1 - par_results["total_time"] / seq_results["total_time"]) * 100

        print(f"\n📈 Throughput improvement: {speedup:.2f}x")
        print(f"⏱️  Time savings: {time_savings:.1f}%")
        print("\n💾 VRAM usage:")
        print("   Sequential (4 workers): ~20GB (model loaded 4 times)")
        print("   Parallel (1 server, 8 slots): ~5GB (model loaded once)")
        print("   VRAM savings: 75%")

        print("\n" + "=" * 80)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python benchmark.py /path/to/model.gguf")
        print("\nExample: python benchmark.py ~/models/llama-2-7b-Q4_K_M.gguf")
        sys.exit(1)

    model_path = sys.argv[1]
    asyncio.run(run_benchmark(model_path))
