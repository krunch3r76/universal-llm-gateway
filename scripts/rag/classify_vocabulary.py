#!/usr/bin/env python3
"""Classify corpus hint terms into vocabulary registers.

Two execution paths:

- **local** (default): Per-scope classification via a single loaded gateway
  model. Each scope's terms + description fit easily in 8–32k context.
  No pipeline overhead, no multi-model consensus.

- **frontier**: Full pipeline (vocab-classify-v1) with per-scope RAG-grounded
  classification via cloud/frontier models.

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
    classify_scope_async,
    pick_loaded_stargate_model,
)

logger = logging.getLogger(__name__)


async def _run_local(
    hints_map: dict[str, str],
    scope_names: list[str],
    config: object,
    model_override: str,
    force: bool,
) -> int:
    """Per-scope classification using a single local model."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        model = model_override or await pick_loaded_stargate_model(client)
        if not model:
            print(
                "No gateway model loaded — cannot classify in local mode.\n"
                "Load a model or use --mode frontier.",
                file=sys.stderr,
            )
            return 1
        print(f"Local model: {model}")

        from services.rag.property_index import PropertyIndex

        idx = PropertyIndex()
        await idx.start()
        try:
            ok: list[str] = []
            failed: list[str] = []
            for scope in scope_names:
                text = hints_map.get(scope, "")
                terms = [t.strip() for t in text.split(",") if t.strip()]
                if not terms:
                    continue
                desc = ""
                if hasattr(config, "scopes"):
                    sdef = config.scopes.get(scope)
                    desc = getattr(sdef, "description", "") or "" if sdef else ""
                result = await classify_scope_async(
                    scope=scope,
                    description=desc,
                    terms=terms,
                    model=model,
                    client=client,
                )
                if result is None:
                    print(f"  FAIL: {scope}")
                    failed.append(scope)
                    await idx.invalidate_scope_freshness(scope)
                    continue
                await idx.replace_scope_vocabulary_for_scopes({scope: result})
                from services.rag.corpus_hints import compute_scope_files_hash

                if hasattr(config, "scopes") and scope in config.scopes:
                    prefixes = list(config.scopes[scope].prefixes)
                    fh = compute_scope_files_hash(idx, prefixes)
                    await idx.store_scope_freshness(scope, fh, classified_tier="local")
                ok.append(scope)
                n_terms = sum(len(v) for v in result.values())
                print(f"  OK: {scope} ({n_terms} terms)")

            if ok:
                await idx.stamp_watermark("vocabulary")
                await idx.stamp_watermark("corpus_hints")
        finally:
            await idx.stop()

    if failed:
        print(
            f"\nCompleted {len(ok)}/{len(ok) + len(failed)} scopes "
            f"({len(failed)} failed)."
        )
    else:
        print(f"\nAll {len(ok)} scopes classified (local).")
    return 0 if not failed else 1


async def _run_frontier(
    scope_names: list[str],
    model_override: str,
    force: bool,
) -> int:
    """Full pipeline classification using frontier/cloud models."""
    pipeline_options: dict = {
        "mode": "frontier",
        "scopes": scope_names,
        "skip_fresh": not force,
    }
    if model_override:
        pipeline_options["model_ref_overrides"] = {"classify": model_override}

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

    mode = (args.mode or config.vocabulary_mode or "local").strip().lower()
    if mode not in ("local", "frontier"):
        print(f"Invalid mode {mode!r} (use local or frontier)", file=sys.stderr)
        return 2

    print(f"Vocabulary classification — {len(scope_names)} scope(s), mode={mode}")
    for s in scope_names:
        desc = getattr(config.scopes.get(s, None), "description", "") or ""
        print(f"  {s}: {desc[:60]}")

    if args.dry_run:
        if mode == "local":
            print("\n--dry-run: would classify per-scope via local model")
            print(f"  model: {args.model or '(auto-detect loaded model)'}")
            print(f"  scopes: {scope_names}")
        else:
            opts: dict = {
                "model": "vocab-classify-v1",
                "mode": mode,
                "scopes": scope_names,
                "skip_fresh": not args.force,
            }
            if args.model:
                opts["model_ref_overrides"] = {"classify": args.model}
            print("\n--dry-run: would POST to Stargate with pipeline_options:")
            print(json.dumps(opts, indent=2))
        return 0

    if not scope_names:
        print("No scopes to classify.", file=sys.stderr)
        return 1

    if mode == "local":
        return await _run_local(hints_map, scope_names, config, args.model, args.force)
    return await _run_frontier(scope_names, args.model, args.force)


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
    except (KeyError, json.JSONDecodeError, IndexError):
        print("vocab-classify-v1 completed (response not parseable).")
        return set()

    dropped = sorted(set(requested_scopes) - written)
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
            f"all {len(requested_scopes)} scopes classified."
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
        help="Override rag.yaml vocabulary_mode (default: config vocabulary_mode)",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Single model ID override (local: gateway model, frontier: cloud model)",
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
