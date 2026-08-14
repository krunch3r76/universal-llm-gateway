"""Filter the SCO All_Records zip for real owner-name rows.

Callers: the `extract` CLI. This is the lawful automated transport — the zip
is a public CloudFront/S3 object on claimit.ca.gov with no Turnstile. Never
invent rows; a surname with zero matches is a completed zero-hit search.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from unclaimed_property_hunter.models import CorpusFingerprint, Hit
from unclaimed_property_hunter.transport import BULK_ZIP_URL

OWNER_COL = "OWNER_NAME"
HOLDER_COL = "HOLDER_NAME"
ID_COL = "PROPERTY_ID"


@dataclass(frozen=True)
class ZipDownloadResult:
    path: Path
    fingerprint: CorpusFingerprint


def download_bulk_zip(dest: Path, *, timeout_s: float = 600.0) -> ZipDownloadResult:
    """Stream ``BULK_ZIP_URL`` to ``dest``; hash bytes while downloading."""
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    hasher = hashlib.sha256()
    last_modified = ""
    etag = ""
    content_length = 0
    with httpx.stream("GET", BULK_ZIP_URL, timeout=timeout_s, follow_redirects=True) as resp:
        resp.raise_for_status()
        last_modified = resp.headers.get("last-modified", "")
        etag = resp.headers.get("etag", "")
        cl = resp.headers.get("content-length")
        if cl and cl.isdigit():
            content_length = int(cl)
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(1024 * 1024):
                hasher.update(chunk)
                fh.write(chunk)
    tmp.replace(dest)
    if not content_length:
        content_length = dest.stat().st_size
    fingerprint = CorpusFingerprint(
        url=BULK_ZIP_URL,
        last_modified=last_modified,
        etag=etag,
        content_length=content_length,
        zip_sha256=hasher.hexdigest(),
    )
    return ZipDownloadResult(path=dest, fingerprint=fingerprint)


def fingerprint_existing_zip(path: Path) -> CorpusFingerprint:
    """Hash an on-disk zip when download was skipped."""
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    return CorpusFingerprint(
        url=BULK_ZIP_URL,
        last_modified="",
        etag="",
        content_length=path.stat().st_size,
        zip_sha256=hasher.hexdigest(),
    )


def _address(row: dict[str, str]) -> str:
    parts = [
        row.get("OWNER_STREET_1", ""),
        row.get("OWNER_STREET_2", ""),
        row.get("OWNER_CITY", ""),
        row.get("OWNER_STATE", ""),
        row.get("OWNER_ZIP", ""),
    ]
    return ", ".join(p.strip() for p in parts if p and p.strip())


def row_to_hit(row: dict[str, str]) -> Hit:
    """Map one SCO CSV row onto the hunter Hit shape. Empty site fields stay empty."""
    cash = (row.get("CURRENT_CASH_BALANCE") or row.get("CASH_REPORTED") or "").strip()
    return Hit(
        property_id=row.get(ID_COL, "").strip(),
        holder=row.get(HOLDER_COL, "").strip(),
        owner_name=row.get(OWNER_COL, "").strip(),
        reported_address=_address(row),
        property_type=row.get("PROPERTY_TYPE", "").strip(),
        amount_or_range=cash,
        escheat_or_report_date="",
    )


def filter_zip_for_surnames(zip_path: Path, surnames: list[str]) -> tuple[list[dict[str, str]], int]:
    """Return (matching rows, rows_scanned) for OWNER_NAME substring matches.

    Matching is case-insensitive. Raises ValueError when the zip has no CSV
    member or the header lacks OWNER_NAME / PROPERTY_ID.
    """
    needles = [s.strip().upper() for s in surnames if s.strip()]
    if not needles:
        raise ValueError("at least one surname is required")
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if not names:
            raise ValueError(f"{zip_path} has no members")
        with zf.open(names[0], "r") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
            reader = csv.reader(text)
            header = next(reader)
            if OWNER_COL not in header or ID_COL not in header:
                raise ValueError(f"unexpected header: {header}")
            owner_idx = header.index(OWNER_COL)
            hits: list[dict[str, str]] = []
            scanned = 0
            for row in reader:
                scanned += 1
                if len(row) <= owner_idx:
                    continue
                owner = row[owner_idx].upper()
                if any(n in owner for n in needles):
                    hits.append(dict(zip(header, row)))
    return hits, scanned


def hits_from_rows(rows: list[dict[str, str]]) -> list[Hit]:
    """Convert filtered SCO rows to Hits, dropping any row without PROPERTY_ID."""
    out: list[Hit] = []
    for row in rows:
        hit = row_to_hit(row)
        if hit.property_id:
            out.append(hit)
    return out
