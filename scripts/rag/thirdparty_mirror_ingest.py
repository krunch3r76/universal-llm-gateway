"""POST article rows and optional forced index for third-party RAG mirror trees."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from thirdparty_mirror_walk import (
    CANONICAL_TIERS,
    FileRecord,
    WalkReport,
    walk_provider,
)
from transport_utils import make_sync_client, resolve_rag_base_url

WORKSPACE = Path(__file__).resolve().parents[2]
THIRDPARTY_ROOT = WORKSPACE / "docs" / "thirdparty"

# Provider directory → display venue + RAG scope.  Scope mirrors `~/.gateway/rag.yaml`.
PROVIDER_VENUES: dict[str, tuple[str, str]] = {
    "xai-api": ("xAI", "xai_api"),
    "claude-api": ("Anthropic", "claude_api"),
    "openai-api": ("OpenAI", "openai_api"),
    "google-api": ("Google", "google_api"),
    "lighter": ("Lighter", "lighter"),
    "coinbase-advanced": ("Coinbase", "coinbase_advanced"),
    "mcp": ("Model Context Protocol", "mcp"),
}


def _build_article_body(
    record: FileRecord, *, venue: str, scope: str
) -> dict[str, str]:
    rel_dir = "/".join(record.rel.split("/")[:-1]) if "/" in record.rel else ""
    subdirectory = (
        f"{record.path.parent.relative_to(THIRDPARTY_ROOT)}"
        if rel_dir
        else record.path.parent.name
    )
    return {
        "source_path": str(record.path.resolve()),
        "filename": record.path.name,
        "title": record.title,
        "authors": "",
        "venue": venue,
        "published_date": record.refreshed,
        "doi": "",
        "abstract": "",
        "content_hash": record.content_hash,
        "subdirectory": subdirectory,
        "scope": scope,
    }


def _upsert_articles(
    *,
    rag_url: str,
    records: list[FileRecord],
    venue: str,
    scope: str,
) -> tuple[int, int, int]:
    created = updated = errors = 0
    with make_sync_client(url=rag_url, timeout=30.0) as client:
        for record in records:
            body = _build_article_body(record, venue=venue, scope=scope)
            try:
                resp = client.post("/article", json=body)
                resp.raise_for_status()
            except Exception as exc:  # transport + http errors handled identically
                errors += 1
                print(f"  ERROR {record.rel}: {exc}")
                continue
            payload = resp.json()
            if payload.get("created"):
                created += 1
            else:
                updated += 1
    return created, updated, errors


def _force_index(*, rag_url: str, records: list[FileRecord]) -> tuple[int, int]:
    indexed = errors = 0
    with make_sync_client(url=rag_url, timeout=600.0) as client:
        for record in records:
            try:
                resp = client.post(
                    "/index",
                    json={"path": str(record.path.resolve()), "force": True},
                )
                resp.raise_for_status()
            except Exception as exc:
                errors += 1
                print(f"  INDEX ERROR {record.rel}: {exc}")
                continue
            data = resp.json()
            indexed += int(data.get("indexed", 0))
    return indexed, errors


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and register third-party API doc mirrors with RAG."
    )
    parser.add_argument(
        "--provider",
        required=True,
        help="Provider directory under docs/thirdparty/ (e.g. xai-api, lighter, mcp)",
    )
    parser.add_argument(
        "--rag-url",
        default=resolve_rag_base_url(),
        help="RAG base URL (default: resolved from stargate.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report only; do not POST /article or /index",
    )
    parser.add_argument(
        "--force-index",
        action="store_true",
        help="POST /index force=true per file (otherwise rely on the RAG watcher)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any validation warnings were produced",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    provider = args.provider.strip().strip("/")
    if not provider:
        raise SystemExit("--provider must be a non-empty directory name")

    venue, scope = PROVIDER_VENUES.get(provider, (provider, provider.replace("-", "_")))
    provider_root = (THIRDPARTY_ROOT / provider).resolve()
    report: WalkReport = walk_provider(provider_root)

    print(f"Provider          : {provider}")
    print(f"Venue / scope     : {venue} / {scope}")
    print(f"Root              : {provider_root}")
    print(f"RAG URL           : {args.rag_url}")
    print(f"Files discovered  : {len(report.files)}")
    tier_counts: dict[str, int] = {}
    for record in report.files:
        tier_counts[record.tier] = tier_counts.get(record.tier, 0) + 1
    for tier in (*CANONICAL_TIERS, "unclassified"):
        if tier in tier_counts:
            print(f"  {tier:<14}: {tier_counts[tier]}")

    if report.warnings:
        print("\nWarnings:")
        for warning in report.warnings:
            print(f"  - {warning}")

    if not report.files:
        print("\nNo content files to register.")
        if args.strict and report.warnings:
            sys.exit(2)
        return

    if args.dry_run:
        print("\n[dry-run] Would upsert articles for:")
        for record in report.files:
            print(f"  {record.tier:<10} {record.rel}  →  {record.title}")
        if args.strict and report.warnings:
            sys.exit(2)
        return

    print("\nUpserting article rows...")
    created, updated, errors = _upsert_articles(
        rag_url=args.rag_url,
        records=report.files,
        venue=venue,
        scope=scope,
    )
    print(f"  Created: {created}")
    print(f"  Updated: {updated}")
    if errors:
        print(f"  Errors : {errors}")

    if args.force_index:
        print("\nForcing reindex per file...")
        indexed, idx_errors = _force_index(rag_url=args.rag_url, records=report.files)
        print(f"  Indexed: {indexed}")
        if idx_errors:
            print(f"  Errors : {idx_errors}")

    if args.strict and (report.warnings or errors):
        sys.exit(2)
