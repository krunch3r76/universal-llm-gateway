"""Estates xlsx token match — fixture workbook, no live SCO download."""

from __future__ import annotations

import zipfile
from pathlib import Path

from unclaimed_property_hunter.estates_extract import (
    filter_xlsx_for_needles,
    hits_from_rows,
    tokens,
)

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _xlsx(path: Path, rows: list[list[str]]) -> Path:
    """Write a minimal shared-string xlsx the estates parser can read."""
    strings: list[str] = []
    index: dict[str, int] = {}

    def sid(text: str) -> int:
        if text not in index:
            index[text] = len(strings)
            strings.append(text)
        return index[text]

    sheet_rows = []
    for r, values in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(values):
            letter = chr(ord("A") + c)
            cells.append(
                f'<c r="{letter}{r}" t="s"><v>{sid(value)}</v></c>'
            )
        sheet_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    si_xml = "".join(f"<si><t>{_xml(s)}</t></si>" for s in strings)
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            f'<?xml version="1.0"?><workbook xmlns="{_NS}">'
            '<sheets><sheet name="Estates" sheetId="1" r:id="rId1" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
            "</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
            "</Relationships>"
        ),
        "xl/sharedStrings.xml": (
            f'<?xml version="1.0"?><sst xmlns="{_NS}" count="{len(strings)}" '
            f'uniqueCount="{len(strings)}">{si_xml}</sst>'
        ),
        "xl/worksheets/sheet1.xml": (
            f'<?xml version="1.0"?><worksheet xmlns="{_NS}">'
            f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
        ),
    }
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return path


def _xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _header() -> list[str]:
    return [
        "Property ID",
        "'E' Number",
        "Name",
        "Relation To Property",
        "Decedent Alias",
        "Amount",
        "CurrentBalance",
        "County",
        "Escheat Date",
    ]


def test_tokens_split_on_non_alnum():
    """MEHRI is a token of MEHRI MANSUBI and not of SHAHMEHRI."""
    assert "MEHRI" in tokens("MEHRI MANSUBI")
    assert "MEHRI" not in tokens("SHAHMEHRI PEGGY KINNEY")
    assert "SHAHMEHRI" in tokens("SHAHMEHRI PEGGY KINNEY")


def test_whole_token_rejects_shahmehri(tmp_path: Path):
    """Whole-token MEHRI rejects SHAHMEHRI and still matches MANSUBI."""
    path = _xlsx(
        tmp_path / "estates.xlsx",
        [
            ["Estates of Deceased Persons File"],
            _header(),
            [
                "970971455",
                "",
                "SHAHMEHRI PEGGY KINNEY",
                "Heir",
                "",
                "783.42",
                "783.42",
                "",
                "",
            ],
            [
                "1",
                "",
                "MANSUBI FRED",
                "Decedent",
                "",
                "10.00",
                "10.00",
                "Santa Clara",
                "",
            ],
        ],
    )
    mehri, scanned = filter_xlsx_for_needles(path, ["MEHRI"])
    assert scanned == 2
    assert mehri == []
    mansubi, _ = filter_xlsx_for_needles(path, ["MANSUBI"])
    assert len(mansubi) == 1
    assert mansubi[0]["Property ID"] == "1"
    hits = hits_from_rows(mansubi)
    assert hits[0].owner_name == "MANSUBI FRED"
    assert hits[0].amount_or_range == "10.00"


def test_substring_needle_finds_estate_of_fred(tmp_path: Path):
    """Optional substring needles catch phrases the token set would miss."""
    path = _xlsx(
        tmp_path / "estates.xlsx",
        [
            _header(),
            [
                "2",
                "",
                "ESTATE OF FRED Q",
                "Decedent",
                "",
                "1",
                "1",
                "",
                "",
            ],
        ],
    )
    rows, scanned = filter_xlsx_for_needles(
        path, [], substring_needles=["ESTATE OF FRED"]
    )
    assert scanned == 1
    assert rows[0]["Property ID"] == "2"


def test_zero_hits_is_completed_empty(tmp_path: Path):
    """A scanned workbook with no needle match returns an empty hit list."""
    path = _xlsx(tmp_path / "estates.xlsx", [_header(), ["9", "", "SMITH", "", "", "", "", "", ""]])
    rows, scanned = filter_xlsx_for_needles(path, ["MANSUBI"])
    assert scanned == 1
    assert rows == []
    assert hits_from_rows(rows) == []
