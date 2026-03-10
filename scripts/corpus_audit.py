#!/usr/bin/env python3
"""
Corpus audit: verify PDF filenames match paper titles and categories are correct.
Walks docs/research/, extracts first-page text via PyMuPDF, batches LLM checks.
Output: TITLE_MISMATCH / WRONG_CATEGORY / DUPLICATE_NAME lines + OK summary per category.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import fitz
import httpx

RESEARCH_ROOT = Path(__file__).resolve().parents[1] / "docs" / "research"
SKIP_DIRS = {"llm-foundations", "prompting"}  # top-level only; prompting/ is empty
EXCERPT_LEN = 600
BATCH_SIZE = 25
GATEWAY_URL = "http://localhost:9999/v1/chat/completions"

CATEGORY_DESCRIPTIONS = """
  belief-consistency/     — belief revision, contradiction handling, confidence/uncertainty, entity resolution
  code-retrieval/         — code retrieval, AST chunking, dependency retrieval, code embeddings
  graph-modeling/         — property graphs, RDF/OWL, schema design, KG construction, query languages
  knowledge-management/  — PKM, personal information architecture, second brain, agent memory
  llm/prompting/         — prompt engineering for large/cloud models
  rag-systems/           — RAG architectures, chunking, embedding, reranking, evaluation
  small_llm/prompting/   — prompt engineering for small/local models
  temporal-provenance/   — bitemporal databases, versioning, data lineage, provenance
  workflows/             — pipeline orchestration, agent workflows, consensus, multi-agent systems
"""


def collect_pdfs() -> list[tuple[str, Path]]:
    """Return list of (rel_path, absolute_path) for each PDF, skipping SKIP_DIRS."""
    out: list[tuple[str, Path]] = []
    for sub in RESEARCH_ROOT.iterdir():
        if not sub.is_dir() or sub.name in SKIP_DIRS:
            continue
        for pdf in sub.rglob("*.pdf"):
            rel = pdf.relative_to(RESEARCH_ROOT)
            out.append((str(rel), pdf))
    return sorted(out, key=lambda x: x[0])


def extract_excerpt(pdf_path: Path) -> str:
    """First EXCERPT_LEN characters of page 0 text."""
    try:
        doc = fitz.open(pdf_path)
        raw = doc[0].get_text() if len(doc) > 0 else ""
        doc.close()
        text = str(raw) if raw else ""
    except Exception as e:
        return f"[ERROR reading PDF: {e}]"
    cleaned = re.sub(r"\s+", " ", text).strip()
    return (cleaned[:EXCERPT_LEN] + "…") if len(cleaned) > EXCERPT_LEN else cleaned


def build_batch_prompt(entries: list[tuple[str, str]]) -> str:
    """Build prompt for one batch of category/filename + excerpt."""
    lines = [
        "You are auditing a research PDF corpus. For each entry below:",
        "1. Does the filename (slug) describe this paper's actual title? If not, output: TITLE_MISMATCH  | {category}/{filename}.pdf | actual_title: \"<title from text>\"",
        "2. Does the paper's primary topic belong in the given category? If not, output: WRONG_CATEGORY  | {category}/{filename}.pdf | title: \"<title>\" | better_category: <suggestion>",
        "3. If this is clearly the same paper as another in the list, output: DUPLICATE_NAME  | path | same_paper_as: other_path",
        "Category meanings:",
        CATEGORY_DESCRIPTIONS,
        "Output ONLY the mismatch lines (one per line). If all entries in the batch are fine, output exactly: OK",
        "",
    ]
    for rel_path, excerpt in entries:
        lines.append(f"--- {rel_path} ---")
        lines.append(excerpt or "(no text)")
        lines.append("")
    return "\n".join(lines)


def call_gateway(prompt: str, model: str = "auto") -> str:
    """POST to gateway, return assistant content."""
    resp = httpx.post(
        GATEWAY_URL,
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        },
        timeout=120,
    )
    if resp.status_code == 404:
        try:
            err = resp.json()
            msg = err.get("error", {}).get("message", "") or str(err)
            if "not found" in msg.lower() or "MODEL_NOT_FOUND" in str(err):
                raise RuntimeError(
                    "No model available for inference. Start a model via ./manage or pass --model <id> (see /v1/models)."
                ) from None
        except (ValueError, TypeError):
            pass
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def parse_response(response: str) -> list[str]:
    """Extract TITLE_MISMATCH / WRONG_CATEGORY / DUPLICATE_NAME lines; ignore OK and other text."""
    reported: list[str] = []
    for line in response.splitlines():
        s = line.strip()
        if not s or s == "OK":
            continue
        if s.startswith("TITLE_MISMATCH  |") or s.startswith("WRONG_CATEGORY  |") or s.startswith("DUPLICATE_NAME  |"):
            reported.append(s)
    return reported


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Audit docs/research PDFs: title vs filename, category.")
    parser.add_argument("--model", default="auto", help="Model ID for LLM checks (default: auto)")
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Only extract excerpts to stdout (JSONL); no LLM calls.",
    )
    args = parser.parse_args()

    pdfs = collect_pdfs()
    if not pdfs:
        print("No PDFs found under docs/research/ (excluding skip dirs).", file=sys.stderr)
        return 1

    if args.extract_only:
        for rel_path, path in pdfs:
            excerpt = extract_excerpt(path)
            print(json.dumps({"path": rel_path, "excerpt": excerpt}))
        return 0

    print(f"Auditing {len(pdfs)} PDFs in batches of {BATCH_SIZE}...", file=sys.stderr)

    all_mismatches: list[str] = []
    by_category: dict[str, list[str]] = {}

    for i in range(0, len(pdfs), BATCH_SIZE):
        batch = pdfs[i : i + BATCH_SIZE]
        entries: list[tuple[str, str]] = []
        for rel_path, path in batch:
            excerpt = extract_excerpt(path)
            entries.append((rel_path, excerpt))
        prompt = build_batch_prompt(entries)
        try:
            response = call_gateway(prompt, model=args.model)
        except Exception as e:
            print(f"Gateway error for batch {i // BATCH_SIZE + 1}: {e}", file=sys.stderr)
            all_mismatches.append(f"# BATCH_ERROR  | batch {i//BATCH_SIZE + 1} | {e}")
            continue
        mismatches = parse_response(response)
        all_mismatches.extend(mismatches)
        for rel_path, _ in entries:
            cat = rel_path.split("/")[0]
            by_category.setdefault(cat, []).append(rel_path)

    # Summary: total per category and OK count (total - mismatches that reference that category)
    categories_seen: dict[str, int] = {}
    for rel_path, _ in pdfs:
        cat = rel_path.split("/")[0]
        categories_seen[cat] = categories_seen.get(cat, 0) + 1

    mismatch_paths: set[str] = set()
    for m in all_mismatches:
        if " | " in m:
            path_part = m.split(" | ", 1)[1].strip().split(" | ")[0].strip()
            mismatch_paths.add(path_part)

    print("\n========== CORPUS AUDIT REPORT ==========\n")
    if all_mismatches:
        print("MISMATCHES (action items):\n")
        for line in all_mismatches:
            print(line)
        print()

    print("SUMMARY BY CATEGORY:\n")
    for cat in sorted(categories_seen.keys()):
        total = categories_seen[cat]
        mismatches_in_cat = sum(1 for p in mismatch_paths if p.startswith(cat + "/"))
        ok = total - mismatches_in_cat
        status = "OK" if mismatches_in_cat == 0 else f"{ok}/{total} correct, {mismatches_in_cat} flagged"
        print(f"  {cat}: {status}")

    print(f"\nTotal PDFs: {len(pdfs)}")
    print(f"Total flagged: {len(mismatch_paths)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
