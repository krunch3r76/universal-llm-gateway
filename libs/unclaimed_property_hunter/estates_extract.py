"""Filter the SCO Estates of Deceased Persons workbook for named tokens.

Callers: the ``estates`` CLI. The workbook is an ungated SCO .xlsx — no
Turnstile. Matching is whole-token on Name and Decedent Alias so a substring
inside another name (SHAHMEHRI vs MEHRI) is not a hit. Optional substring
needles cover EIN spellings and phrases such as ESTATE OF FRED.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from unclaimed_property_hunter.models import CorpusFingerprint, Hit
from unclaimed_property_hunter.surfaces import ESTATES_XLSX_URL

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_TOKEN_RE = re.compile(r"[A-Z0-9]+")

NAME_COL = "Name"
ALIAS_COL = "Decedent Alias"
ID_COL = "Property ID"


@dataclass(frozen=True)
class XlsxDownloadResult:
    """On-disk workbook plus HTTP/content fingerprint — not the workbook bytes."""

    path: Path
    fingerprint: CorpusFingerprint


def download_estates_xlsx(dest: Path, *, timeout_s: float = 120.0) -> XlsxDownloadResult:
    """Stream the SCO estates workbook to ``dest`` and hash bytes on the way."""
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    hasher = hashlib.sha256()
    last_modified = ""
    etag = ""
    content_length = 0
    with httpx.stream(
        "GET", ESTATES_XLSX_URL, timeout=timeout_s, follow_redirects=True
    ) as resp:
        resp.raise_for_status()
        last_modified = resp.headers.get("last-modified", "")
        etag = resp.headers.get("etag", "")
        cl = resp.headers.get("content-length")
        if cl and cl.isdigit():
            content_length = int(cl)
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(1024 * 64):
                hasher.update(chunk)
                fh.write(chunk)
    tmp.replace(dest)
    if not content_length:
        content_length = dest.stat().st_size
    fingerprint = CorpusFingerprint(
        url=ESTATES_XLSX_URL,
        last_modified=last_modified,
        etag=etag,
        content_length=content_length,
        zip_sha256=hasher.hexdigest(),
        corpus_source="state_download",
    )
    return XlsxDownloadResult(path=dest, fingerprint=fingerprint)


def fingerprint_existing_xlsx(path: Path) -> CorpusFingerprint:
    """Hash an on-disk estates workbook when the caller skipped download."""
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 64), b""):
            hasher.update(chunk)
    return CorpusFingerprint(
        url=f"file://{path.resolve().as_posix()}",
        last_modified="",
        etag="",
        content_length=path.stat().st_size,
        zip_sha256=hasher.hexdigest(),
        corpus_source="local_disk",
    )


def tokens(text: str) -> set[str]:
    """Uppercase alphanumeric tokens — the unit the estates search matches."""
    return set(_TOKEN_RE.findall(text.upper()))


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    out: list[str] = []
    for si in root.findall("m:si", _NS):
        out.append("".join(t.text or "" for t in si.findall(".//m:t", _NS)))
    return out


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    value = cell.find("m:v", _NS)
    if value is None or value.text is None:
        return ""
    if kind == "s":
        idx = int(value.text)
        if 0 <= idx < len(shared):
            return shared[idx]
        return ""
    return value.text


def _col_letter(ref: str) -> str:
    return "".join(ch for ch in ref if ch.isalpha())


def iter_xlsx_rows(xlsx_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Return (header, data rows) from the first worksheet.

    Title rows before the header are skipped until a row contains ``Property ID``.
    """
    with zipfile.ZipFile(xlsx_path) as zf:
        shared = _shared_strings(zf)
        sheet_names = [n for n in zf.namelist() if n.startswith("xl/worksheets/")]
        if not sheet_names:
            raise ValueError(f"{xlsx_path} has no worksheet")
        root = ET.fromstring(zf.read(sorted(sheet_names)[0]))
    header: list[str] = []
    header_letters: list[str] = []
    rows: list[dict[str, str]] = []
    for row in root.findall("m:sheetData/m:row", _NS):
        cells: dict[str, str] = {}
        for cell in row.findall("m:c", _NS):
            ref = cell.get("r") or ""
            cells[_col_letter(ref)] = _cell_text(cell, shared)
        if not header:
            ordered = [cells.get(letter, "") for letter in sorted(cells, key=_col_key)]
            if ID_COL in ordered:
                header_letters = sorted(cells, key=_col_key)
                header = [cells.get(letter, "") for letter in header_letters]
            continue
        rows.append(
            {name: cells.get(letter, "") for name, letter in zip(header, header_letters)}
        )
    if not header:
        raise ValueError(f"{xlsx_path} has no header containing {ID_COL!r}")
    return header, rows


def _col_key(letter: str) -> tuple[int, str]:
    n = 0
    for ch in letter:
        n = n * 26 + (ord(ch.upper()) - 64)
    return (n, letter)


def filter_xlsx_for_needles(
    xlsx_path: Path,
    token_needles: list[str],
    substring_needles: list[str] | None = None,
) -> tuple[list[dict[str, str]], int]:
    """Return (matching rows, rows_scanned) for token and optional substring needles."""
    tokens_need = [t.strip().upper() for t in token_needles if t.strip()]
    subs = [s.strip().upper() for s in (substring_needles or []) if s.strip()]
    if not tokens_need and not subs:
        raise ValueError("at least one needle is required")
    _header, rows = iter_xlsx_rows(xlsx_path)
    hits: list[dict[str, str]] = []
    for row in rows:
        name = row.get(NAME_COL, "")
        alias = row.get(ALIAS_COL, "")
        hay_tokens = tokens(name) | tokens(alias)
        hay_sub = f"{name} {alias}".upper()
        token_hit = any(n in hay_tokens for n in tokens_need)
        sub_hit = any(n in hay_sub for n in subs)
        if token_hit or sub_hit:
            hits.append(row)
    return hits, len(rows)


def row_to_hit(row: dict[str, str]) -> Hit:
    """Map one estates workbook row onto the hunter Hit shape."""
    cash = (row.get("CurrentBalance") or row.get("Amount") or "").strip()
    return Hit(
        property_id=row.get(ID_COL, "").strip(),
        holder="",
        owner_name=row.get(NAME_COL, "").strip(),
        reported_address=row.get("County", "").strip(),
        property_type=row.get("Relation To Property", "").strip(),
        amount_or_range=cash,
        escheat_or_report_date=row.get("Escheat Date", "").strip(),
    )


def hits_from_rows(rows: list[dict[str, str]]) -> list[Hit]:
    """Convert filtered estates rows to Hits, dropping rows without Property ID."""
    out: list[Hit] = []
    for row in rows:
        hit = row_to_hit(row)
        if hit.property_id:
            out.append(hit)
    return out
