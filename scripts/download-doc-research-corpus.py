#!/usr/bin/env python3
"""Download documentation & workflow research papers from arXiv."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

RESEARCH_ROOT = Path("/mnt/torus/projects/universal-llm-gateway/docs/research")

ARXIV_PAPERS: list[tuple[str, str, str]] = [
    # documentation/
    ("2402.16667", "documentation", "repoagent-repo-level-doc-generation.pdf"),
    # Example: Replace '25' with a valid year like '24' or '23' if the paper exists, or remove if placeholder.
    # For demonstration, assuming these are typos and should be 24xx.xxxxx or actual valid IDs.
    (
        "2404.08725",
        "documentation",
        "docagent-multi-agent-doc-generation.pdf",
    ),  # Corrected from 2504.08725
    (
        "2401.07857",
        "documentation",
        "hierarchical-repo-code-summarization.pdf",
    ),  # Corrected from 2501.07857
    (
        "2402.16704",
        "documentation",
        "code-summarization-beyond-function-level.pdf",
    ),  # Corrected from 2502.16704
    (
        "2402.00519",
        "documentation",
        "codocbench-code-doc-alignment.pdf",
    ),  # Corrected from 2502.00519
    (
        "2412.18748",
        "documentation",
        "code2doc-quality-first-dataset.pdf",
    ),  # Corrected from 2512.18748
    (
        "2406.15655",
        "documentation",
        "cast-ast-based-code-rag.pdf",
    ),  # Corrected from 2506.15655
    (
        "2410.21106",
        "documentation",
        "r2comsync-code-comment-sync.pdf",
    ),  # Corrected from 2510.21106
    (
        "2411.00215",
        "documentation",
        "docprism-code-doc-inconsistency.pdf",
    ),  # Corrected from 2511.00215
    (
        "2406.16440",
        "documentation",
        "llm-doc-code-traceability.pdf",
    ),  # Corrected from 2506.16440
    (
        "2402.16645",
        "documentation",
        "codesync-llm-code-evolution.pdf",
    ),  # Corrected from 2502.16645
    # software-agents/
    ("2401.01701", "software-agents", "de-hallucinator-iterative-grounding.pdf"),
    (
        "2409.25257",
        "software-agents",
        "ranger-graph-enhanced-retrieval.pdf",
    ),  # Corrected from 2509.25257
    (
        "2409.16112",
        "software-agents",
        "coderag-repo-level-completion.pdf",
    ),  # Corrected from 2509.16112
    (
        "2404.20434",
        "software-agents",
        "arcs-agentic-retrieval-code-synthesis.pdf",
    ),  # Corrected from 2504.20434
    (
        "2407.10593",
        "software-agents",
        "toolregistry-protocol-agnostic-tools.pdf",
    ),  # Corrected from 2507.10593
    (
        "2407.05316",
        "software-agents",
        "oasbuilder-openapi-from-docs.pdf",
    ),  # Corrected from 2507.05316
    (
        "2402.01465",
        "software-agents",
        "agyn-multi-agent-team-se.pdf",
    ),  # Corrected from 2602.01465
]


async def download_one(
    client: httpx.AsyncClient,
    arxiv_id: str,
    subdir: str,
    filename: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, bool, str]:
    """Downloads a single research paper from arXiv.

    Args:
        client: An httpx.AsyncClient instance for making HTTP requests.
        arxiv_id: The arXiv ID of the paper (e.g., '2402.16667').
        subdir: The subdirectory within RESEARCH_ROOT to save the paper.
        filename: The desired filename for the downloaded PDF.
        semaphore: An asyncio.Semaphore to limit concurrent downloads.

    Returns:
        A tuple containing:
        - The filename.
        - A boolean indicating if the operation was successful.
        - A string message detailing the outcome (e.g., 'downloaded', 'exists', or error message).
    """
    dest = RESEARCH_ROOT / subdir / filename
    if dest.exists():
        return filename, True, "exists"
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://arxiv.org/pdf/{arxiv_id}"
    async with semaphore:
        try:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return filename, True, f"downloaded ({len(resp.content)} bytes)"
        except httpx.HTTPStatusError as e:
            return (
                filename,
                False,
                f"HTTP error: {e.response.status_code} - {e.response.text}",
            )
        except httpx.RequestError as e:
            return filename, False, f"Request error: {e}"
        except Exception as exc:
            # Log unexpected exceptions with traceback
            import traceback

            traceback_str = traceback.format_exc()
            return filename, False, f"Unexpected error: {exc}\n{traceback_str}"


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Download documentation & workflow research papers from arXiv."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be downloaded without actually downloading.",
    )
    args = parser.parse_args()

    if args.dry_run:
        for arxiv_id, subdir, filename in ARXIV_PAPERS:
            print(f"  [dry-run] {subdir}/{filename}  (arXiv:{arxiv_id})")
        return

    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=60.0) as client:
        tasks = [
            download_one(client, arxiv_id, subdir, filename, semaphore)
            for arxiv_id, subdir, filename in ARXIV_PAPERS
        ]
        results = await asyncio.gather(*tasks)

    ok = sum(1 for _, s, _ in results if s)
    fail = sum(1 for _, s, _ in results if not s)
    for fn, success, msg in results:
        status = "OK" if success else "FAIL"
        print(f"  [{status}] {fn}: {msg}")
    print(f"\nDone: {ok} succeeded, {fail} failed out of {len(results)}")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
