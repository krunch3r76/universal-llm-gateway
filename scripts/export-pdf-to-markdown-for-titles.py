#!/usr/bin/env python3
"""
Convert PDFs that lack a title in the article registry to markdown in a
temporary directory for inspection, and extract a candidate title from each.

Output:
  tmp/article-registry-titles/{subdirectory}/{stem}.md  — first 2 pages only (for title inspection)
  tmp/article-registry-titles/manifest.yaml             — filename -> candidate_title, path (for review)

Title extraction:
  Without --llm: heuristic (first # heading or first non-empty line).
  With --llm: call Stargate chat/completions. Model default: first local model from /v1/models, else openrouter/free (no local needed).

Usage:
  python scripts/export-pdf-to-markdown-for-titles.py --llm --limit 3     # test on 3 PDFs (local or openrouter/free)
  python scripts/export-pdf-to-markdown-for-titles.py --llm --model openrouter/free  # force free cloud model
  python scripts/export-pdf-to-markdown-for-titles.py --llm --apply       # extract and update registry
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import yaml

REGISTRY_PATH = Path.home() / ".rag" / "article_registry.yaml"
RESEARCH_ROOT = Path(__file__).resolve().parents[1] / "docs/research"
OUT_ROOT = Path(__file__).resolve().parents[1] / "tmp/article-registry-titles"
MANIFEST_PATH = OUT_ROOT / "manifest.yaml"
STARGATE_URL = "http://localhost:9999/v1/chat/completions"
MODELS_URL = "http://localhost:9999/v1/models"
LLM_EXTRACT_MAX_CHARS = 3500
# Fallback when no local model: free tier on OpenRouter (Stargate cloud proxy must be configured).
OPENROUTER_FREE = "openrouter/free"
# Cheap cloud model for sampling (used in consult, modularize, code_review). Low cost, good for title extraction.
CHEAP_CLOUD_SAMPLE = "google/gemini-2.5-flash"


def resolve_llm_model(timeout: float = 5.0) -> str:
    """Prefer local: first model from /v1/models with no '/' in id. Else openrouter/free."""
    try:
        resp = httpx.get(MODELS_URL, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        for obj in data.get("data") or []:
            mid = (obj.get("id") or "").strip()
            if mid and "/" not in mid:
                return mid
    except Exception:
        return OPENROUTER_FREE
    return OPENROUTER_FREE


def extract_title_via_llm(
    md_text: str, model: str = "auto", timeout: float = 60.0
) -> str:
    """Ask the gateway LLM to return only the paper title from the given text. Empty on failure."""
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
                    {
                        "role": "system",
                        "content": "You return only the requested piece of text, with no extra wording.",
                    },
                    {"role": "user", "content": f"{prompt}\n\n---\n\n{excerpt}"},
                ],
                "temperature": 0,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get(
            "content"
        ) or ""
        return content.strip()
    except Exception:
        return ""


def extract_candidate_title(md_text: str) -> str:
    """First # heading, or first non-empty line (skip arXiv/URL-like lines)."""
    lines = md_text.splitlines()
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            return re.sub(r"^#+\s*", "", s).strip()
        if re.match(r"^(arXiv|http|https|www\.)\s*[:.]?", s, re.I):
            continue
        if len(s) > 400:
            return s[:397] + "..."
        return s
    return ""


def pdf_to_markdown(pdf_path: Path, first_pages_only: bool = True) -> str:
    """Convert PDF to markdown. If first_pages_only, only pages 0–1 (title/abstract)."""
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
    return str(raw)


def main(
    apply: bool = False,
    use_llm: bool = False,
    model: str = "auto",
    limit: int | None = None,
) -> None:
    registry_raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    articles: dict[str, dict] = dict(registry_raw.get("articles", {}))

    # PDFs with no title and with subdirectory (so we have a path)
    to_convert: list[tuple[str, dict]] = []
    for filename, entry in articles.items():
        if not filename.endswith(".pdf"):
            continue
        if entry.get("title"):
            continue
        sub = entry.get("subdirectory") or ""
        if not sub:
            continue
        to_convert.append((filename, entry))

    if limit is not None and limit > 0:
        to_convert = to_convert[:limit]
        print(f"Limiting to first {limit} PDFs (test run).", flush=True)

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    if use_llm:
        model = resolve_llm_model() if model == "auto" else model
        scope = "local" if "/" not in model else "cloud"
        if scope == "local":
            print(
                f"Title extraction: LLM (model={model}, local). Stargate must be running on localhost:9999.",
                flush=True,
            )
        else:
            print(
                f"Title extraction: LLM (model={model}). Stargate must be running on localhost:9999.",
                flush=True,
            )
    manifest: dict[str, dict[str, str]] = {}
    n = len(to_convert)
    for idx, (filename, entry) in enumerate(to_convert, 1):
        sub = entry.get("subdirectory", "")
        pdf_path = RESEARCH_ROOT / sub / filename
        if not pdf_path.exists():
            manifest[filename] = {
                "path": str(pdf_path),
                "candidate_title": "",
                "error": "file_not_found",
            }
            continue
        stem = pdf_path.stem
        out_dir = OUT_ROOT / sub
        _ = out_dir.mkdir(parents=True, exist_ok=True)
        out_md = out_dir / f"{stem}.md"
        try:
            md_text = pdf_to_markdown(pdf_path)
            _ = out_md.write_text(md_text, encoding="utf-8")
            if use_llm:
                candidate = extract_title_via_llm(md_text, model=model)
                if not candidate:
                    candidate = extract_candidate_title(md_text)
            else:
                candidate = extract_candidate_title(md_text)
            manifest[filename] = {
                "path": str(pdf_path),
                "out_md": str(out_md),
                "candidate_title": candidate,
            }
            if apply and candidate:
                entry["title"] = candidate
                print(f"  [{idx}/{n}] {filename[:45]}... -> {candidate[:50]}...")
        except Exception as e:
            manifest[filename] = {
                "path": str(pdf_path),
                "candidate_title": "",
                "error": str(e),
            }

    _ = MANIFEST_PATH.write_text(
        yaml.dump(
            manifest, default_flow_style=False, allow_unicode=True, sort_keys=True
        ),
        encoding="utf-8",
    )
    print(f"Wrote {len(to_convert)} markdown files under {OUT_ROOT}")
    print(f"Manifest: {MANIFEST_PATH}")

    if apply:
        out_articles = {
            k: {kk: vv for kk, vv in v.items() if vv not in (None, "")}
            for k, v in sorted(articles.items())
        }
        header = (
            "# Article registry for RAG chunk metadata (title, published_date, content_hash, etc.).\n"
            "# Loaded by RAG service when article_registry_path is set in ~/.gateway/rag.yaml.\n"
            "# Keys are source filenames (basename). content_hash = SHA-256 of file bytes.\n"
            "# Duplicate basenames across dirs: one entry per basename (first path wins).\n\n"
        )
        _ = REGISTRY_PATH.write_text(
            header
            + yaml.dump(
                {"articles": out_articles},
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        print("Updated article_registry.yaml with candidate titles.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Export PDFs (no title) to markdown and extract title."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write candidate titles into article_registry.yaml",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Use Stargate LLM to extract title from md (more reliable)",
    )
    parser.add_argument(
        "--model",
        default="auto",
        help="Model for --llm (default: auto = local else openrouter/free). Cheap cloud: google/gemini-2.5-flash. Free: openrouter/free.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process only first N PDFs (for testing)",
    )
    args = parser.parse_args()
    main(apply=args.apply, use_llm=args.llm, model=args.model, limit=args.limit)
