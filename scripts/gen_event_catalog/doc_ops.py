"""In-place doc adoption: overlay extraction, marker wrapping, region patching."""

from __future__ import annotations

import re
from pathlib import Path

from .extract import FactoryRecord
from .render import render_region

_STD_HEADER = "| Signal | Required Payload | Optional Payload |"
_MCP_HEADER = "| Signal | Payload fields | Description |"
_REQ_DESC_HEADER = "| Signal | Required Payload | Description |"

_WRAP_SECTIONS = (
    "## Signal Reference",
    "## mcp.adapter.*",
    "## cloudproxy.mcp.*",
    "## MCP Server Signals",
    "## MCP Stdio Proxy Signals",
    "## Management API Signals",
    "## Manage GPU image build signals",
    "## Relay socket-dir recovery signal",
    "## Fleet Operation Signals",
    "## System Signals",
)


def _section_allowed(title: str) -> bool:
    if title in _WRAP_SECTIONS:
        return True
    return any(p.endswith("*") and title.startswith(p[:-1]) for p in _WRAP_SECTIONS)


def _section_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """Return (start, end) line indices for wrap-eligible ## sections."""
    h2_lines = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    ranges: list[tuple[int, int]] = []
    for idx, start in enumerate(h2_lines):
        if not _section_allowed(lines[start]):
            continue
        end = h2_lines[idx + 1] if idx + 1 < len(h2_lines) else len(lines)
        ranges.append((start, end))
    return ranges


def _in_ranges(i: int, ranges: list[tuple[int, int]]) -> bool:
    return any(s <= i < e for s, e in ranges)


_REGION_START = re.compile(
    r"<!-- GENERATED:START region=(?P<region>[\w-]+)(?:\s+inventory_sha=\S+)?(?:\s+generated=\S+)?\s*-->"
)
_REGION_END = re.compile(r"<!-- GENERATED:END(?:\s+region=[\w-]+)?\s*-->")


def _escape_toml(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def extract_standard_overlay(text: str) -> dict[str, str]:
    """Scrape col-3 from standard catalog tables (Task 1b)."""
    overlay: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip() != _STD_HEADER:
            i += 1
            continue
        i += 2
        while i < len(lines) and lines[i].startswith("|"):
            parts = [p.strip() for p in lines[i].strip().strip("|").split("|")]
            if len(parts) >= 3 and parts[0].startswith("`"):
                sig = parts[0].strip("`")
                opt = parts[2]
                if opt and opt != "-":
                    overlay[sig] = opt
            i += 1
    return overlay


def extract_mcp_overlay(text: str) -> dict[str, str]:
    """Map MCP Description column → optional_payload overlay."""
    overlay: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        hdr = lines[i].strip()
        if hdr not in (_MCP_HEADER, _REQ_DESC_HEADER):
            i += 1
            continue
        i += 2
        while i < len(lines) and lines[i].startswith("|"):
            parts = [p.strip() for p in lines[i].strip().strip("|").split("|")]
            if len(parts) >= 3 and parts[0].startswith("`"):
                sig = parts[0].strip("`~")
                if sig.startswith("~~") or "retired" in parts[0].lower():
                    i += 1
                    continue
                desc = parts[-1]
                if desc and desc != "-":
                    overlay[sig] = desc
            i += 1
    return overlay


def build_overlay(text: str) -> dict[str, str]:
    overlay = extract_standard_overlay(text)
    overlay.update(extract_mcp_overlay(text))
    return overlay


def write_overlay(path: Path, overlay: dict[str, str]) -> None:
    lines = ["[optional_payload]"]
    for sig in sorted(overlay):
        lines.append(f'"{sig}" = "{_escape_toml(overlay[sig])}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_table_rows(lines: list[str], start: int) -> tuple[list[str], int]:
    """Return (data rows, end index) for a table starting at header line `start`."""
    rows: list[str] = []
    i = start + 2
    while i < len(lines) and lines[i].startswith("|"):
        rows.append(lines[i])
        i += 1
    return rows, i


def _row_signal(row: str) -> str | None:
    m = re.match(r"^\|\s*~?`([^`]+)`", row)
    return m.group(1) if m else None


def _domain(signal: str) -> str:
    return signal.split(".")[0] if "." in signal else signal


def _wrap_rows_by_domain(rows: list[str], inventory_sha: str) -> list[str]:
    """Split table rows into per-domain GENERATED blocks."""
    by_domain: dict[str, list[str]] = {}
    for row in rows:
        sig = _row_signal(row)
        if sig is None:
            continue
        by_domain.setdefault(_domain(sig), []).append(row)

    out: list[str] = []
    header = _STD_HEADER + "\n|--------|------------------|------------------|"
    for domain in sorted(by_domain):
        body = (
            header
            + "\n"
            + "\n".join(sorted(by_domain[domain], key=lambda r: _row_signal(r) or ""))
        )
        out.append(
            f"<!-- GENERATED:START region={domain} inventory_sha={inventory_sha} -->"
        )
        out.append(body)
        out.append(f"<!-- GENERATED:END region={domain} -->")
    return out


def wrap_standard_tables(text: str, inventory_sha: str = "pending") -> str:
    """Replace standard 3-col catalog tables with per-domain GENERATED blocks."""
    lines = text.splitlines()
    ranges = _section_ranges(lines)
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == _STD_HEADER and _in_ranges(i, ranges):
            rows, end = _parse_table_rows(lines, i)
            out.extend(_wrap_rows_by_domain(rows, inventory_sha))
            i = end
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def wrap_mcp_tables(text: str, inventory_sha: str = "pending") -> str:
    """Wrap MCP-format tables in eligible sections; normalize header on regen."""
    lines = text.splitlines()
    ranges = _section_ranges(lines)
    out: list[str] = []
    i = 0
    while i < len(lines):
        hdr = lines[i].strip()
        if hdr in (_MCP_HEADER, _REQ_DESC_HEADER) and _in_ranges(i, ranges):
            rows, end = _parse_table_rows(lines, i)
            std_rows: list[str] = []
            for row in rows:
                parts = [p.strip() for p in row.strip().strip("|").split("|")]
                if len(parts) < 3 or not parts[0].startswith("`"):
                    continue
                sig_raw = parts[0]
                if "~~" in sig_raw or "retired" in sig_raw.lower():
                    continue
                sig = sig_raw.strip("`")
                req = parts[1]
                opt = parts[2]
                std_rows.append(f"| `{sig}` | {req} | {opt} |")
            out.extend(_wrap_rows_by_domain(std_rows, inventory_sha))
            i = end
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def iter_regions(text: str):
    """Yield (region_id, start_line, end_line, inner_text) for each GENERATED block."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _REGION_START.match(lines[i].strip())
        if not m:
            i += 1
            continue
        region = m.group("region")
        start = i
        i += 1
        inner: list[str] = []
        while i < len(lines):
            if _REGION_END.match(lines[i].strip()):
                yield region, start, i, "\n".join(inner)
                i += 1
                break
            inner.append(lines[i])
            i += 1


def patch_doc(
    doc_path: Path,
    records: list[FactoryRecord],
    overlay: dict[str, str],
    inventory_sha: str,
) -> None:
    """Replace each GENERATED region body with freshly rendered domain table."""
    text = doc_path.read_text(encoding="utf-8")
    by_domain: dict[str, list[FactoryRecord]] = {}
    for r in records:
        by_domain.setdefault(r.domain, []).append(r)

    lines = text.splitlines()
    replacements: list[tuple[int, int, list[str]]] = []
    for region, start, end, _ in iter_regions(text):
        recs = by_domain.get(region, [])
        rendered = render_region(region, recs, overlay, inventory_sha).splitlines()
        replacements.append((start, end, rendered))

    for start, end, new_block in reversed(replacements):
        lines[start : end + 1] = new_block

    doc_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def region_signals(inner: str) -> set[str]:
    sigs: set[str] = set()
    for line in inner.splitlines():
        if not line.startswith("|") or line.startswith("|--------"):
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if not parts or not parts[0].startswith("`"):
            continue
        sig = parts[0].strip("`~")
        if "." in sig and sig[0].isalpha():
            sigs.add(sig)
    return sigs


def verify_generated_spans(text: str) -> list[str]:
    """Assert GENERATED spans contain only table rows + markers (normalization gate)."""
    errors: list[str] = []
    for region, _start, _end, inner in iter_regions(text):
        for line in inner.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("|"):
                continue
            errors.append(
                f"region={region}: non-table line inside GENERATED span: {s[:80]}"
            )
    return errors
