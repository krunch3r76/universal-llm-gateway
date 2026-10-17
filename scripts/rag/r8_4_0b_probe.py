#!/usr/bin/env python3
"""Read-only 4.0b helpers: latency probes, cosine vs baseline, model-status reads.

Does not unload/load models. Operator-run after maestro ACK and overlay backup.
"""

from __future__ import annotations

import argparse
import json
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
    allowed_gateway_ids: list[str] | None,
) -> dict:
    headers = {"X-Request-Timeout": str(int(timeout_s))}
    body: dict = {"model": model, "input": inputs}
    if allowed_gateway_ids:
        body["allowed_gateway_ids"] = allowed_gateway_ids
    resp = client.post("/v1/embeddings", json=body, headers=headers)
    resp.raise_for_status()
    return resp.json()


def _latency_probe(
    client: httpx.Client,
    *,
    model: str,
    batch: list[str],
    n: int,
    timeout_s: float,
    allowed: list[str] | None,
) -> tuple[float, float]:
    samples: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        _post_embeddings(
            client, model=model, inputs=batch, timeout_s=timeout_s, allowed_gateway_ids=allowed
        )
        samples.append(time.perf_counter() - start)
    return statistics.median(samples), sorted(samples)[max(0, int(0.99 * len(samples)) - 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG r8 4.0b read-only probe helpers")
    parser.add_argument("--stargate", default="http://localhost:9999")
    parser.add_argument("--model", default="qwen3-embedding-8b-q8-0-8192")
    parser.add_argument("--allowed-gateway-ids", nargs="*", default=None)
    parser.add_argument("--baseline", type=Path, help="Saved baseline embeddings JSON")
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
            allowed=args.allowed_gateway_ids,
        )
        p50_batch, p99_batch = _latency_probe(
            client,
            model=args.model,
            batch=texts[:16],
            n=args.n,
            timeout_s=45.0,
            allowed=args.allowed_gateway_ids,
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
        if args.baseline:
            from services.rag.embeddings.cosine_similarity import cosine_similarity

            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            live = _post_embeddings(
                client,
                model=args.model,
                inputs=texts,
                timeout_s=45.0,
                allowed_gateway_ids=args.allowed_gateway_ids,
            )
            live_vecs = [row["embedding"] for row in live["data"]]
            for i, (a, b) in enumerate(zip(baseline["vectors"], live_vecs, strict=True)):
                sim = cosine_similarity(a, b)
                print(f"cosine[{i}]={sim:.6f}")


if __name__ == "__main__":
    main()
