#!/usr/bin/env python3
"""Report which PDFs in the research corpus are not yet indexed in ChromaDB.

Usage:
    python tools/pipeline_test/check_corpus.py
    python tools/pipeline_test/check_corpus.py --corpus docs/research/prompting
"""

from __future__ import annotations

import argparse
from pathlib import Path

import chromadb


def check_corpus(corpus_dir: Path) -> None:
    client = chromadb.PersistentClient(path=str(Path.home() / ".rag" / "store"))
    col = client.get_collection("knowledge")

    results = col.get(include=["metadatas"])
    indexed: dict[str, dict] = {}
    for meta in results["metadatas"]:
        if not isinstance(meta, dict):
            continue
        source = meta.get("source")
        if isinstance(source, str) and source.endswith(".pdf"):
            # Keep one metadata entry per source (arbitrary chunk, just need date)
            if source not in indexed:
                indexed[source] = meta

    on_disk = sorted(corpus_dir.glob("*.pdf"))
    if not on_disk:
        print(f"No PDFs found in {corpus_dir}")
        return

    not_indexed: list[Path] = []
    indexed_no_date: list[tuple[Path, str]] = []
    indexed_with_date: list[tuple[Path, str, str]] = []

    for pdf in on_disk:
        key = str(pdf.resolve())
        if key not in indexed:
            not_indexed.append(pdf)
        else:
            pub = indexed[key].get("published_date")
            if pub:
                indexed_with_date.append((pdf, key, str(pub)))
            else:
                indexed_no_date.append((pdf, key))

    if not_indexed:
        print(f"\n{'=' * 60}")
        print(f"NOT INDEXED ({len(not_indexed)} files):")
        print(f"{'=' * 60}")
        for pdf in not_indexed:
            print(f"  {pdf.name}")
        print()
        print("Index with date:")
        corpus_abs = corpus_dir.resolve()
        for pdf in not_indexed:
            print(
                f"  curl -s -X POST --unix-socket /tmp/universal-protocol/rag.sock "
                f"http://localhost/index \\\n"
                f"    -H 'Content-Type: application/json' \\\n"
                f'    -d \'{{"path": "{corpus_abs}/{pdf.name}", "metadata_overrides": {{"published_date": "YYYY-MM-DDT00:00:00+00:00"}}}}\''
            )
    else:
        print("\nAll PDFs are indexed.")

    if indexed_no_date:
        print(f"\n{'=' * 60}")
        print(f"INDEXED BUT NO published_date ({len(indexed_no_date)} files):")
        print(f"{'=' * 60}")
        for pdf, _ in indexed_no_date:
            print(f"  {pdf.name}")

    print(f"\n{'=' * 60}")
    print(f"INDEXED WITH DATE ({len(indexed_with_date)} files):")
    print(f"{'=' * 60}")
    for pdf, _, pub in indexed_with_date:
        print(f"  {pdf.name:60s}  {pub[:10]}")

    print(
        f"\nTotal on disk: {len(on_disk)}  |  Not indexed: {len(not_indexed)}  |  Missing date: {len(indexed_no_date)}  |  Complete: {len(indexed_with_date)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        default="docs/research/prompting",
        help="Path to corpus directory (default: docs/research/prompting)",
    )
    args = parser.parse_args()
    corpus_dir = Path(args.corpus)
    if not corpus_dir.exists():
        print(f"Corpus directory not found: {corpus_dir}")
        raise SystemExit(1)
    check_corpus(corpus_dir)


if __name__ == "__main__":
    main()
