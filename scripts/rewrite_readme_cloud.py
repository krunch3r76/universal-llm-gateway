#!/usr/bin/env python3
"""One-off: send README.md to a cloud model for revision.

Usage:
  CLOUD_MODEL=anthropic/claude-sonnet-4-20250514 python scripts/rewrite_readme_cloud.py
  # Or use default from OPENROUTER / allow_prefixes

Reads README.md from repo root, POSTs to Stargate /v1/chat/completions with the
given cloud model, writes revised content to README_REVISED.md (or stdout with -o -).

Requires: Stargate running with cloud proxy and a cloud model available. Set CLOUD_MODEL to a model ID that appears in your cloud proxy allow_prefixes.

**Models that tend to add punch for writing:** openai/gpt-5.4, openai/gpt-5.4-pro, anthropic/claude-opus-4.5, anthropic/claude-opus-4.6, anthropic/claude-sonnet-4.5, anthropic/claude-sonnet-4.6, google/gemini-2.5-pro, openai/o3, openai/o1-pro. Use --overview to generate only a short TL;DR for the top of the README.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
README_PATH = ROOT / "README.md"
STARGATE_URL = os.environ.get("STARGATE_URL", "http://localhost:9999")
DEFAULT_MODEL = os.environ.get(
    "CLOUD_MODEL",
    "anthropic/claude-sonnet-4-20250514",
)
TIMEOUT = 300.0

SYSTEM_PROMPT = """You are revising the README for an open-source, privacy-first LLM inference stack (Universal LLM Gateway). Keep all factual content, links, tables, and code blocks accurate. Improve clarity and punch. Emphasize:

1. **Privacy-first and hardening by construction** — data stays on your hardware; outbound internet only via an optional single process; no telemetry.
2. **Novel approach: LLM prompt-driven pseudo-tooling** — the model outputs structured decisions (e.g. JSON); the engine maps them to pre-defined operations executed in a hardened sidecar. Models never invoke tools directly. Contrast with optional traditional pathways (native tool-calling not yet supported; MCP under active development), both hardened when present.
3. **Docker and minimal TCP** — edge nodes run as Docker containers with network_mode: none (Unix socket only). Cloud proxy runs as a separate container/process, talking to Stargate over UDS. Minimally exposed TCP: only :9999 for Stargate (client-facing); optionally :443 for MCP. Gateway is never exposed (container-internal). Relay on remote nodes also :9999 for Master→Relay only.

Keep the "Documentation status" callout. OUTPUT ONLY THE REVISED README: your entire response must be the complete markdown document from the first # heading through the final line. Do not include any summary, preamble, or meta-commentary. Do not say \"here is the revised version\" or explain your changes. Only output the raw revised README text."""

OVERVIEW_PROMPT = """You are writing a very short overview for the README of Universal LLM Gateway — an open-source, privacy-first LLM inference stack.

Given the README below, write a single paragraph of 2–4 sentences that gives a strong, concise TL;DR. It should capture: (1) privacy-first local inference, nothing leaves your hardware by default; (2) the novel approach — prompt-driven pseudo-tooling with a hardened sidecar, not direct model tool-calling; (3) minimal exposure — one client port, containers and UDS. Punchy and scannable. Output only that paragraph, no heading, no preamble, no quotes around it."""


def _run_overview(model: str, readme_text: str) -> int:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": OVERVIEW_PROMPT},
            {"role": "user", "content": readme_text[:12000]},
        ],
        "max_tokens": 300,
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                f"{STARGATE_URL.rstrip('/')}/v1/chat/completions",
                json=body,
            )
            r.raise_for_status()
            data = r.json()
    except httpx.ConnectError as e:
        print(f"Could not reach Stargate: {e}", file=sys.stderr)
        return 1
    except httpx.HTTPStatusError as e:
        print(
            f"HTTP {e.response.status_code}: {e.response.text[:500]}", file=sys.stderr
        )
        return 1
    choices = data.get("choices", [])
    if not choices:
        print("No choices in response", file=sys.stderr)
        return 1
    content = choices[0].get("message", {}).get("content", "").strip()
    if not content:
        print("Empty overview", file=sys.stderr)
        return 1
    print(content)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Rewrite README via cloud model")
    parser.add_argument(
        "-o",
        "--output",
        default="README_REVISED.md",
        help="Output path (default: README_REVISED.md); use - for stdout",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=DEFAULT_MODEL,
        help="Cloud model ID (default: CLOUD_MODEL or anthropic/claude-sonnet-4-20250514)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print prompt and exit without calling API",
    )
    parser.add_argument(
        "--overview",
        action="store_true",
        help="Generate only a 2–4 sentence concise overview (TL;DR) for the top of the README; output to stdout",
    )
    args = parser.parse_args()

    if not README_PATH.exists():
        print(f"README not found: {README_PATH}", file=sys.stderr)
        return 1

    readme_text = README_PATH.read_text(encoding="utf-8")

    if args.overview:
        return _run_overview(args.model, readme_text)

    if args.dry_run:
        print("System prompt:", SYSTEM_PROMPT[:200], "...", file=sys.stderr)
        print("User message length:", len(readme_text), file=sys.stderr)
        return 0

    body = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": readme_text},
        ],
        "max_tokens": 16000,
    }

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            r = client.post(
                f"{STARGATE_URL.rstrip('/')}/v1/chat/completions",
                json=body,
            )
            r.raise_for_status()
            data = r.json()
    except httpx.ConnectError as e:
        print(f"Could not reach Stargate at {STARGATE_URL}: {e}", file=sys.stderr)
        return 1
    except httpx.HTTPStatusError as e:
        print(
            f"HTTP {e.response.status_code}: {e.response.text[:500]}", file=sys.stderr
        )
        return 1

    choices = data.get("choices", [])
    if not choices:
        print("No choices in response", file=sys.stderr)
        return 1

    content = choices[0].get("message", {}).get("content", "")
    if not content:
        print("Empty content in response", file=sys.stderr)
        return 1

    # Strip optional markdown code fence
    content = content.strip()
    if content.startswith("```markdown"):
        content = content[10:].lstrip("\n")
    elif content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content.rsplit("```", 1)[0].rstrip()

    if args.output == "-":
        print(content)
    else:
        out_path = (
            ROOT / args.output
            if not Path(args.output).is_absolute()
            else Path(args.output)
        )
        out_path.write_text(content, encoding="utf-8")
        print(f"Wrote {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
