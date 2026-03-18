#!/usr/bin/env python3
"""
Audit article registry: for every PDF in the registry, extract title from the PDF
via LLM and compare to the registered title. Flag inconsistencies (mismatch or
missing).

Runs in batches; requires Stargate and a model (default: first local from /v1/models else openrouter/free).

Usage:
  python scripts/audit-registry-titles.py
  python scripts/audit-registry-titles.py --model qwen3-1-7b-q8-0-40960 --batch-size 5
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import httpx
import yaml

REGISTRY_PATH = Path.home() / ".rag" / "article_registry.yaml"
RESEARCH_ROOT = Path(__file__).resolve().parents[1] / "docs/research"
OUT_DIR = Path(__file__).resolve().parents[1] / "tmp/article-registry-titles"
STARGATE_URL = "http://localhost:9999/v1/chat/completions"
LLM_EXTRACT_MAX_CHARS = 3500
DEFAULT_BATCH_SIZE = 8
BATCH_DELAY_S = 0.5


def normalize_title(s: str) -> str:
    """Lowercase, collapse whitespace, remove markdown bold."""
    if not s:
        return ""
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def titles_consistent(registered: str, extracted: str) -> bool:
    """True if both empty, or one contains the other, or high word overlap."""
    r = normalize_title(registered)
    e = normalize_title(extracted)
    if not r:
        return True
    if not e:
        return False
    if r == e:
        return True
    if r in e or e in r:
        return True
    rw = set(re.findall(r"\w+", r))
    ew = set(re.findall(r"\w+", e))
    if not rw:
        return not ew
    overlap = len(rw & ew) / len(rw)
    return overlap >= 0.6


def pdf_to_markdown(pdf_path: Path, first_pages_only: bool = True) -> str:
    try:
        import pymupdf4llm
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency. Install with: pip install pymupdf4llm pymupdf-layout"
        ) from exc
    kwargs: dict = {}
    if first_pages_only:
        kwargs["pages"] = [0, 1]
    raw = pymupdf4llm.to_markdown(str(pdf_path), **kwargs)
    if isinstance(raw, list):
        return "\n\n".join(str(item) for item in raw)
    return raw if isinstance(raw, str) else str(raw)


def extract_title_via_llm(md_text: str, model: str, timeout: float = 60.0) -> str:
    excerpt = md_text.strip()[:LLM_EXTRACT_MAX_CHARS]
    if not excerpt:
        return ""
    prompt = (
        "From the following text (first pages of an academic PDF converted to markdown), "
        "extract the paper title and nothing else. Return only the title, no quotes, no explanation, no prefix."
    )
    try:
        resp = httpx.post(
            STARGATE_URL,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You return only the requested piece of text, with no extra wording."},
                    {"role": "user", "content": f"{prompt}\n\n---\n\n{excerpt}"},
                ],
                "temperature": 0,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        return content.strip()
    except Exception as e:
        print(f"Error extracting title via LLM: {e}", file=sys.stderr)
        return ""


def resolve_model(timeout: float = 5.0) -> str:
    try:
        resp = httpx.get("http://localhost:9999/v1/models", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        for obj in data.get("data") or []:
            mid = (obj.get("id") or "").strip()
            if mid and "/" not in mid:
                return mid
    except Exception as e:
        print(
            f"Warning: Could not resolve local model: {e}. Falling back to openrouter/free.",
            file=sys.stderr,
        )
    return "openrouter/free"


def main(model: str = "auto", batch_size: int = DEFAULT_BATCH_SIZE) -> None:
    """Audit article registry titles against LLM-extracted titles from PDFs.

    Args:
        model: The LLM model to use for title extraction. 'auto' attempts to find
            a local model, otherwise defaults to 'openrouter/free'.
        batch_size: The number of PDFs to process in each batch.
    """
    registry_raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    articles: dict[str, dict] = dict(registry_raw.get("articles", {}))

    pdf_entries: list[tuple[str, dict]] = []
    for filename, entry in articles.items():
        if not filename.endswith(".pdf"):
            continue
        sub = entry.get("subdirectory") or ""
        if not sub:
            continue
        pdf_entries.append((filename, entry))

    effective_model = resolve_model() if model == "auto" else model
    print(f"Auditing {len(pdf_entries)} PDFs in batches of {batch_size} (model={effective_model})", flush=True)

    inconsistencies: list[dict[str, str]] = []
    total = len(pdf_entries)
    for i in range(0, total, batch_size):
        batch = pdf_entries[i : i + batch_size]
        for filename, entry in batch:
            pdf_path = RESEARCH_ROOT / (entry.get("subdirectory") or "") / filename
            if not pdf_path.exists():
                inconsistencies.append({
                    "file": filename,
                    "registered_title": entry.get("title") or "",
                    "extracted_title": "",
                    "reason": "file_not_found",
                })
                continue
            try:
                md_text = pdf_to_markdown(pdf_path)
                extracted = extract_title_via_llm(md_text, effective_model)
            except Exception as e:
                inconsistencies.append({
                    "file": filename,
                    "registered_title": entry.get("title") or "",
                    "extracted_title": "",
                    "reason": str(e),
                })
                continue
            registered = entry.get("title") or ""
            if not registered and extracted:
                inconsistencies.append({
                    "file": filename,
                    "registered_title": "",
                    "extracted_title": extracted,
                    "reason": "missing_in_registry",
                })
            elif not titles_consistent(registered, extracted):
                inconsistencies.append({
                    "file": filename,
                    "registered_title": registered,
                    "extracted_title": extracted,
                    "reason": "mismatch",
                })
        idx = min(i + batch_size, total)
        print(f"  Processed {idx}/{total}...", flush=True)
        if i + batch_size < total:
            time.sleep(BATCH_DELAY_S)

    # Report
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUT_DIR / "audit-inconsistencies.yaml"
    report = {
        "total_pdfs": total,
        "inconsistencies": inconsistencies,
        "model_used": effective_model,
    }
    report_path.write_text(
        yaml.dump(report, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    print(f"\nInconsistencies: {len(inconsistencies)}", flush=True)
    print(f"Report: {report_path}", flush=True)
    for item in inconsistencies:
        print(f"  [{item['reason']}] {item['file']}", flush=True)
        if item.get("registered_title"):
            print(f"      registered: {item['registered_title'][:70]}...", flush=True)
        if item.get("extracted_title"):
            print(f"      extracted:  {item['extracted_title'][:70]}...", flush=True)

    if inconsistencies:
        sys.exit(1)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Audit registry titles against LLM-extracted titles from PDFs.")
    parser.add_argument("--model", default="auto", help="Model id (default: auto = local else openrouter/free)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"PDFs per batch (default {DEFAULT_BATCH_SIZE})")
    args = parser.parse_args()
    main(model=args.model, batch_size=args.batch_size)
