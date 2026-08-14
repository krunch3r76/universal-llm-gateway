"""Filter the SCO All_Records zip for real owner-name rows.

Callers: the `extract` CLI. This is the lawful automated transport — the zip
is a public CloudFront/S3 object on claimit.ca.gov with no Turnstile. Never
invent rows; a surname with zero matches is a completed zero-hit search.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from unclaimed_property_hunter.models import Hit
from unclaimed_property_hunter.transport import BULK_ZIP_URL

OWNER_COL = "OWNER_NAME"
HOLDER_COL = "HOLDER_NAME"
ID_COL = "PROPERTY_ID"


def download_bulk_zip(dest: Path, *, timeout_s: float = 600.0) -> Path:
    """Stream `BULK_ZIP_URL` to `dest` and return the path.

    Overwrites `dest`. Caller supplies the cache location; this function does
    not invent a default under /tmp.
    """
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", BULK_ZIP_URL, timeout=timeout_s, follow_redirects=True) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(1024 * 1024):
                fh.write(chunk)
    tmp.replace(dest)
    return dest


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
