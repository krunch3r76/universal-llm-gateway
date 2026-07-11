"""Source manifest composition for RAG recon theme sidecars."""

from __future__ import annotations

import re
from typing import Any

_SOURCE_HEADER_RE = re.compile(
    r"^\[Source:\s*(.+?)(?:\s*\|\s*Last changed:.*)?\]$"
)
_BRACKET_META_RE = re.compile(r"^\[.*\]$")
_SKIP_NOTE = "no relevant hits (below MARGINAL threshold)"


def relevance_tag(chunks_found: int, content_length: int) -> str:
    if chunks_found >= 3 or content_length >= 1200:
        return "RELEVANT"
    if chunks_found >= 1 or content_length >= 200:
        return "MARGINAL"
    return "SKIP"


def parse_source_headers(context: str) -> list[dict[str, Any]]:
    """Return label and 1-based context_line for each ``[Source:`` header."""
    headers: list[dict[str, Any]] = []
    for line_no, line in enumerate(context.splitlines(), start=1):
        if not line.startswith("[Source:"):
            continue
        match = _SOURCE_HEADER_RE.match(line)
        label = match.group(1).strip() if match else line.removeprefix("[Source:").strip()
        headers.append({"label": label, "context_line": line_no})
    return headers


def source_lead(context: str, header_line: int) -> str:
    """First evidence line after ``[Body evidence]`` or the source header."""
    lines = context.splitlines()
    start = header_line
    for idx in range(header_line, len(lines)):
        if lines[idx].strip() == "[Body evidence]":
            start = idx + 1
            break
    for idx in range(start, len(lines)):
        candidate = lines[idx].strip()
        if not candidate:
            continue
        if _BRACKET_META_RE.match(candidate):
            continue
        return candidate[:120]
    return ""


def query_md_path(theme: str, query: str, tag: str | None) -> str:
    if tag:
        return f"Theme: {theme}/Results/Query: {query} [{tag}]"
    return f"Theme: {theme}/Results/Query: {query}"


def _build_source_manifest_markdown(manifest_entries: list[dict[str, Any]]) -> list[str]:
    lines = ["", "## Source manifest", ""]
    for entry in manifest_entries:
        query = entry["query"]
        tag = entry.get("tag")
        heading = f"### Query: {query} [{tag}]" if tag else f"### Query: {query}"
        lines.extend([heading, f"- md_path: `{entry['md_path']}`"])
        if entry.get("sources"):
            for source in entry["sources"]:
                lines.append(
                    f"- `{source['label']}` · line={source['line']} · lead: {source['lead']}"
                )
        elif entry.get("error"):
            lines.append(f"- sources: _(none — search failed: {entry['error']})_")
        elif entry.get("note"):
            lines.append(f"- sources: _(none — {entry['note']})_")
        else:
            lines.append("- sources: _(none)_")
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    return lines


def _apply_line_offset(
    manifest_entries: list[dict[str, Any]],
    offset: int,
) -> None:
    for entry in manifest_entries:
        for source in entry.get("sources", []):
            source["line"] += offset


def _compact_source_manifest(
    manifest_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for entry in manifest_entries:
        item: dict[str, Any] = {
            "query": entry["query"],
            "tag": entry.get("tag"),
            "md_path": entry["md_path"],
            "sources": [
                {
                    "label": source["label"],
                    "line": source["line"],
                    "lead": source["lead"],
                }
                for source in entry.get("sources", [])
            ],
        }
        if entry.get("error"):
            item["error"] = entry["error"]
        if entry.get("note"):
            item["note"] = entry["note"]
        compact.append(item)
    return compact


def _build_results_section(
    *,
    theme: str,
    queries: list[str],
    query_results: list[dict[str, Any]],
    header_line_count: int,
    discards_section: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Build Results+Discards lines and per-query manifest structs (body-relative lines)."""
    results_lines = ["", "## Results", ""]
    manifest_entries: list[dict[str, Any]] = []

    for query, result in zip(queries, query_results, strict=False):
        if result.get("error"):
            error = str(result["error"])
            results_lines.extend(
                [
                    f"### Query: {query}",
                    "",
                    f"_Search failed: {error}_",
                    "",
                ]
            )
            manifest_entries.append(
                {
                    "query": query,
                    "tag": None,
                    "md_path": query_md_path(theme, query, None),
                    "error": error,
                    "note": None,
                    "sources": [],
                }
            )
            continue

        tag = relevance_tag(
            int(result.get("chunks_found") or 0),
            int(result.get("content_length") or 0),
        )
        context = result.get("context") or "_No results._"
        results_lines.extend([f"### Query: {query} [{tag}]", "", context, ""])

        context_start_line = header_line_count + len(results_lines) - 1
        sources: list[dict[str, Any]] = []
        if context != "_No results._":
            for header in parse_source_headers(context):
                lead = source_lead(context, header["context_line"])
                sources.append(
                    {
                        "label": header["label"],
                        "line": context_start_line + header["context_line"] - 1,
                        "lead": lead,
                    }
                )

        note = _SKIP_NOTE if tag == "SKIP" and not sources else None
        manifest_entries.append(
            {
                "query": query,
                "tag": tag,
                "md_path": query_md_path(theme, query, tag),
                "error": None,
                "note": note,
                "sources": sources,
            }
        )

    results_lines.append(discards_section)
    return results_lines, manifest_entries


def build_theme_markdown(
    *,
    theme: str,
    scopes: list[str],
    queries: list[str],
    query_results: list[dict[str, Any]],
    discards_section: str,
    frontmatter_line_count: int = 0,
) -> tuple[str, list[dict[str, Any]]]:
    header_lines = [
        f"# Theme: {theme}",
        "",
        "## Scopes",
        *[f"- {scope}" for scope in scopes],
        "",
        "## Queries",
    ]
    for idx, query in enumerate(queries, start=1):
        header_lines.append(f"{idx}. {query}")

    header_line_count = len(header_lines)
    results_lines, manifest_entries = _build_results_section(
        theme=theme,
        queries=queries,
        query_results=query_results,
        header_line_count=header_line_count,
        discards_section=discards_section,
    )

    manifest_line_count = len(_build_source_manifest_markdown(manifest_entries))
    _apply_line_offset(manifest_entries, manifest_line_count)
    if frontmatter_line_count:
        _apply_line_offset(manifest_entries, frontmatter_line_count)

    manifest_lines = _build_source_manifest_markdown(manifest_entries)
    body_lines = header_lines + manifest_lines + results_lines
    body = "\n".join(body_lines).rstrip() + "\n"
    return body, _compact_source_manifest(manifest_entries)
