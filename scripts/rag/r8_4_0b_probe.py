#!/usr/bin/env python3
"""Read-only 4.0b helpers: latency probes, cosine vs baseline, model-status reads.

Does not unload/load models. Operator-run after maestro ACK and overlay backup.

Gateway pinning: Stargate ignores ``allowed_gateway_ids`` on the embeddings body;
use a single loaded copy or route via model catalog / federation config.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path

import httpx

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "services/rag/tests/fixtures/embedding_cosine_20_texts.json"
)


def _post_embeddings(
    client: httpx.Client,
    *,
    model: str,
    inputs: list[str],
    timeout_s: float,
) -> dict:
    headers = {
        "X-Request-Timeout": str(timeout_s),
        "X-Embedding-Class": "batch",
    }
    body: dict = {"model": model, "input": inputs}
    resp = client.post("/v1/embeddings", json=body, headers=headers)
    resp.raise_for_status()
    return resp.json()


def _p99(samples: list[float]) -> float:
    if not samples:
        return 0.0
    n = len(samples)
    idx = max(0, math.ceil(0.99 * n) - 1)
    return sorted(samples)[idx]


def _latency_probe(
    client: httpx.Client,
    *,
    model: str,
    batch: list[str],
    n: int,
    timeout_s: float,
) -> tuple[float, float]:
    samples: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        _post_embeddings(client, model=model, inputs=batch, timeout_s=timeout_s)
        samples.append(time.perf_counter() - start)
    return statistics.median(samples), _p99(samples)


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG r8 4.0b read-only probe helpers")
    parser.add_argument("--stargate", default="http://localhost:9999")
    parser.add_argument("--model", default="qwen3-embedding-8b-q8-0-8192")
    parser.add_argument("--baseline", type=Path, help="Saved baseline embeddings JSON")
    parser.add_argument(
        "--save-baseline",
        type=Path,
        help="Write live embedding vectors JSON for later cosine compare",
    )
    parser.add_argument("--n", type=int, default=50)
    args = parser.parse_args()

    texts = json.loads(FIXTURE.read_text(encoding="utf-8"))["texts"]
    with httpx.Client(base_url=args.stargate, timeout=120.0) as client:
        p50_single, p99_single = _latency_probe(
            client,
            model=args.model,
            batch=[texts[0]],
            n=args.n,
            timeout_s=45.0,
        )
        p50_batch, p99_batch = _latency_probe(
            client,
            model=args.model,
            batch=texts[:16],
            n=args.n,
            timeout_s=45.0,
        )
        status = client.get(f"/api/v1/model-status/{args.model}").json()
        print(
            json.dumps(
                {
                    "single_embed_s": {"p50": p50_single, "p99": p99_single},
                    "batch_16_embed_s": {"p50": p50_batch, "p99": p99_batch},
                    "model_status_hardware": status.get("hardware"),
                },
                indent=2,
            )
        )
        if args.save_baseline:
            live = _post_embeddings(
                client,
                model=args.model,
                inputs=texts,
                timeout_s=45.0,
            )
            live_vecs = [row["embedding"] for row in live["data"]]
            args.save_baseline.write_text(
                json.dumps({"vectors": live_vecs}, indent=2),
                encoding="utf-8",
            )
            print(f"Wrote baseline vectors to {args.save_baseline}")
        if args.baseline:
            from services.rag.embeddings.cosine_similarity import cosine_similarity

            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            live = _post_embeddings(
                client,
                model=args.model,
                inputs=texts,
                timeout_s=45.0,
            )
            live_vecs = [row["embedding"] for row in live["data"]]
            for i, (a, b) in enumerate(zip(baseline["vectors"], live_vecs, strict=True)):
                sim = cosine_similarity(a, b)
                print(f"cosine[{i}]={sim:.6f}")


if __name__ == "__main__":
    main()
