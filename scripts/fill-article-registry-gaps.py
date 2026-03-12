#!/usr/bin/env python3
"""
Fill article_registry.yaml gaps: remove stale .html entries and populate
title, authors, published_date from arXiv for PDFs listed in restore-research-corpus.py.

Usage: python scripts/fill-article-registry-gaps.py
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import yaml

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "docs/research/article_registry.yaml"
RESTORE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/restore-research-corpus.py"
ARXIV_API = "https://export.arxiv.org/api/query"
BATCH_SIZE = 20
DELAY_S = 1.0


def extract_arxiv_ids_from_restore_script() -> dict[str, str]:
    """Parse restore-research-corpus.py for (arxiv_id, target_dir, filename); return filename -> arxiv_id (first wins)."""
    text = RESTORE_SCRIPT.read_text(encoding="utf-8")
    # Match ("2312.10997", "rag-systems", "gao-rag-survey-2024.pdf"),
    pattern = re.compile(r'\s*\(\s*"([0-9]+\.[0-9]+)"\s*,\s*"[^"]+"\s*,\s*"([^"]+)"\s*\)')
    result: dict[str, str] = {}
    for m in pattern.finditer(text):
        arxiv_id, filename = m.group(1), m.group(2)
        if filename.endswith(".pdf") and filename not in result:
            result[filename] = arxiv_id
    return result


def fetch_arxiv_metadata(arxiv_ids: list[str]) -> dict[str, dict[str, str]]:
    """Batch fetch arXiv API; return arxiv_id -> {title, authors, published_date}."""
    out: dict[str, dict[str, str]] = {}
    for i in range(0, len(arxiv_ids), BATCH_SIZE):
        batch = arxiv_ids[i : i + BATCH_SIZE]
        id_list = ",".join(batch)
        resp = httpx.get(ARXIV_API, params={"id_list": id_list}, timeout=30.0)
        resp.raise_for_status()
        _ = time.sleep(DELAY_S)
        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        for entry in root.findall(".//atom:entry", ns):
            id_elem = entry.find("atom:id", ns)
            if id_elem is None:
                continue
            id_text = id_elem.text
            if not id_text:
                continue
            # id looks like http://arxiv.org/abs/2404.16130 or .../2404.16130v2
            raw_id = id_text.strip().split("/")[-1]
            arxiv_id = raw_id.split("v")[0]  # drop version suffix for lookup
            title_elem = entry.find("atom:title", ns)
            title = (title_elem.text or "").strip().replace("\n", " ")
            authors_elems = entry.findall(".//atom:author/atom:name", ns)
            authors = ", ".join((a.text or "").strip() for a in authors_elems[:5])
            if len(authors_elems) > 5:
                authors += " et al."
            published_elem = entry.find("atom:published", ns)
            published = (published_elem.text or "")[:10] if published_elem is not None else ""
            out[arxiv_id] = {"title": title, "authors": authors, "published_date": published}
    return out


def main() -> None:
    registry_raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    articles: dict[str, dict] = dict(registry_raw.get("articles", {}))

    # 1. Remove stale .html entries
    to_drop = [k for k in articles if k.endswith(".html")]
    for k in to_drop:
        del articles[k]
    print(f"Dropped {len(to_drop)} stale .html entries")

    # 2. Filename -> arXiv ID from restore script
    filename_to_arxiv = extract_arxiv_ids_from_restore_script()
    print(f"Found {len(filename_to_arxiv)} PDFs with arXiv IDs in restore script")

    # 3. Which registry PDFs need metadata and which arXiv IDs to fetch
    arxiv_ids_to_fetch: list[str] = []
    pdfs_needing_meta: list[str] = []
    for filename, entry in articles.items():
        if not filename.endswith(".pdf"):
            continue
        if entry.get("title"):
            continue
        aid = filename_to_arxiv.get(filename)
        if aid:
            arxiv_ids_to_fetch.append(aid)
            pdfs_needing_meta.append(filename)

    arxiv_ids_to_fetch = list(dict.fromkeys(arxiv_ids_to_fetch))

    print(f"Fetching metadata for {len(arxiv_ids_to_fetch)} unique arXiv IDs...")
    meta_by_id = fetch_arxiv_metadata(arxiv_ids_to_fetch)

    # 4. Fill registry (each filename in pdfs_needing_meta has an arxiv_id)
    filled = 0
    for filename in pdfs_needing_meta:
        arxiv_id = filename_to_arxiv[filename]
        meta = meta_by_id.get(arxiv_id)
        if not meta or filename not in articles:
            continue
        entry = articles[filename]
        if entry.get("title"):
            continue
        entry["title"] = meta["title"]
        entry["authors"] = meta["authors"]
        if meta["published_date"]:
            entry["published_date"] = meta["published_date"]
        filled += 1

    print(f"Filled title/authors/published_date for {filled} entries")

    # 5. Write back (preserve order and style: only non-empty values)
    def clean_entry(e: dict) -> dict:
        return {k: v for k, v in e.items() if v not in (None, "")}

    out_articles = {k: clean_entry(v) for k, v in sorted(articles.items())}
    out = {
        "articles": out_articles,
    }
    header = """# Article registry for RAG chunk metadata (title, published_date, content_hash, etc.).
# Loaded by RAG service when article_registry_path is set in ~/.gateway/rag.yaml.
# Keys are source filenames (basename). content_hash = SHA-256 of file bytes.
# Duplicate basenames across dirs: one entry per basename (first path wins).

"""
    _ = REGISTRY_PATH.write_text(
        header + yaml.dump(out, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"Wrote {REGISTRY_PATH}")


if __name__ == "__main__":
    main()
