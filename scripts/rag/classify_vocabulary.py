#!/usr/bin/env python3
"""Classify corpus hint terms into vocabulary registers.

Both local and frontier modes run through the vocab-classify-v1 pipeline:

- **local**: pipeline mode="local", model resolved from rag.yaml
  ``vocabulary_model`` (override via ``--model``). Uses the
  ``domain_discovery`` model alias defined in ``pipelines/vocab_classify/models.yaml``.

- **frontier**: pipeline mode="frontier", uses the ``frontier_classify``
  model alias (override via ``--model``).

Usage:
    python scripts/rag/classify_vocabulary.py [--mode local|frontier] [--force]
    python scripts/rag/classify_vocabulary.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

import httpx

from services.rag.config import load_config
from services.rag.corpus_hints import load_corpus_hints
from services.rag.vocabulary import (
    DEFAULT_STARGATE_CHAT_URL,
    _resolve_scope_vocab_mode,
)

logger = logging.getLogger(__name__)


async def _run_pipeline(
    scope_names: list[str],
    mode: str,
    model: str,
    force: bool,
) -> int:
    """Classify scopes via vocab-classify-v1 pipeline (local or frontier mode)."""
    pipeline_options: dict = {
        "mode": mode,
        "scopes": scope_names,
        "skip_fresh": not force,
    }
    if model:
        pipeline_options["model_ref_overrides"] = {"classify": model}

    payload: dict = {
        "model": "vocab-classify-v1",
        "messages": [{"role": "user", "content": "vocabulary classification"}],
        "pipeline_options": pipeline_options,
    }

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(3600.0),
        ) as client:
            resp = await client.post(DEFAULT_STARGATE_CHAT_URL, json=payload)
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.exception(
            "vocab-classify-v1 failed: status %d: %s",
            e.response.status_code,
            e.response.text,
        )
        return 1
    except httpx.RequestError as e:
        logger.exception("vocab-classify-v1 network error: %s", e)
        return 1
    except Exception:
        logger.exception("vocab-classify-v1 unexpected error")
        return 1

    written = _report_partial_success(resp, scope_names)
    return 0 if written is not None else 1


async def _main_async(args: argparse.Namespace) -> int:
    config = load_config()
    hints_map = load_corpus_hints()

    if not hints_map:
        print(
            "No corpus hints found. Run `python -m services.rag.corpus_hints` first.",
            file=sys.stderr,
        )
        return 1

    exclude = set(args.exclude or [])
    scope_names = sorted(
        s for s in hints_map if s not in exclude and hints_map.get(s, "").strip()
    )

    if not scope_names:
        print("No scopes to classify.", file=sys.stderr)
        return 1

    # --mode overrides per-scope vocab_mode; without it, each scope uses its own setting.
    if args.mode:
        mode_override = args.mode.strip().lower()
        local_scopes = scope_names if mode_override == "local" else []
        frontier_scopes = scope_names if mode_override == "frontier" else []
        print(
            f"Vocabulary classification — {len(scope_names)} scope(s),"
            f" mode={mode_override} (--mode override)"
        )
    else:
        local_scopes = sorted(
            s for s in scope_names if _resolve_scope_vocab_mode(s, config) == "local"
        )
        frontier_scopes = sorted(
            s for s in scope_names if _resolve_scope_vocab_mode(s, config) == "frontier"
        )
        print(
            f"Vocabulary classification — {len(scope_names)} scope(s)"
            f" ({len(local_scopes)} local, {len(frontier_scopes)} frontier)"
        )

    for s in scope_names:
        effective = args.mode or _resolve_scope_vocab_mode(s, config)
        desc = getattr(config.scopes.get(s, None), "description", "") or ""
        print(f"  [{effective}] {s}: {desc[:60]}")

    # Resolve models: --model overrides for both modes; local falls back to
    # vocabulary_model from rag.yaml (which maps to domain_discovery in the pipeline).
    local_model = args.model or config.vocabulary_model or ""
    frontier_model = args.model or ""

    if args.dry_run:
        if local_scopes:
            opts: dict = {
                "model": "vocab-classify-v1",
                "mode": "local",
                "scopes": local_scopes,
                "skip_fresh": not args.force,
            }
            if local_model:
                opts["model_ref_overrides"] = {"classify": local_model}
            print("\n--dry-run: would POST to Stargate with pipeline_options (local):")
            print(json.dumps(opts, indent=2))
            if not local_model:
                print(
                    "  WARNING: no local model — set vocabulary_model in rag.yaml"
                    " or pass --model",
                    file=sys.stderr,
                )
        if frontier_scopes:
            opts = {
                "model": "vocab-classify-v1",
                "mode": "frontier",
                "scopes": frontier_scopes,
                "skip_fresh": not args.force,
            }
            if frontier_model:
                opts["model_ref_overrides"] = {"classify": frontier_model}
            print(
                "\n--dry-run: would POST to Stargate with pipeline_options (frontier):"
            )
            print(json.dumps(opts, indent=2))
        return 0

    rc = 0
    if local_scopes:
        if not local_model:
            print(
                "No model for local scopes — set vocabulary_model in rag.yaml"
                " or pass --model.",
                file=sys.stderr,
            )
            rc |= 1
        else:
            rc |= await _run_pipeline(local_scopes, "local", local_model, args.force)
    if frontier_scopes:
        rc |= await _run_pipeline(
            frontier_scopes, "frontier", frontier_model, args.force
        )
    return rc


def _report_partial_success(
    resp: httpx.Response,
    requested_scopes: list[str],
) -> set[str] | None:
    """Parse pipeline response and report scope-level results."""
    try:
        choices = resp.json().get("choices", [])
        content = choices[0]["message"]["content"] if choices else "{}"
        result = json.loads(content)
        vocab = result.get("vocabulary", {})
        written = set(vocab.keys())
        # Scopes skipped by the pipeline because they were already fresh at the
        # requested tier. These are NOT failures — freshness should not be invalidated.
        skipped_fresh: set[str] = set(result.get("skipped_fresh") or [])
    except (KeyError, json.JSONDecodeError, IndexError):
        print("vocab-classify-v1 completed (response not parseable).")
        return set()

    handled = written | skipped_fresh
    dropped = sorted(set(requested_scopes) - handled)
    if skipped_fresh:
        print(
            f"Skipped {len(skipped_fresh)} scope(s) already fresh at frontier tier: "
            + ", ".join(sorted(skipped_fresh))
        )
    if dropped:
        print(
            f"WARNING: {len(dropped)} scope(s) failed "
            f"(freshness invalidated — will retry next run):"
        )
        for s in dropped:
            print(f"  - {s}")
        print(f"Completed {len(written)}/{len(requested_scopes)} scopes.")
    else:
        print(
            f"vocab-classify-v1 completed: "
            f"all {len(requested_scopes)} scopes classified "
            f"({len(written)} classified, {len(skipped_fresh)} already fresh)."
            if skipped_fresh
            else f"vocab-classify-v1 completed: all {len(requested_scopes)} scopes classified."
        )
    return written


def main() -> None:
    """Entry point for vocabulary classification."""
    parser = argparse.ArgumentParser(
        description="Classify corpus hints into vocabulary registers"
    )
    parser.add_argument(
        "--mode",
        choices=("local", "frontier"),
        default=None,
        help="Force all scopes to this mode, overriding per-scope vocab_mode settings",
    )
    parser.add_argument(
        "--model",
        default="",
        help=(
            "Model ID override (local: overrides vocabulary_model from rag.yaml;"
            " frontier: overrides frontier_classify alias)"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Set skip_fresh=false (reclassify despite tier/hash)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned operation only",
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=[],
        help="Scope names to exclude",
    )
    args = parser.parse_args()
    code = asyncio.run(_main_async(args))
    raise SystemExit(code)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    main()
