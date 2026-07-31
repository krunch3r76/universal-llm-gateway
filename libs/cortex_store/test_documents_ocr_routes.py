"""Smoke/regression tests for cortex-api OCR document routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cortex_store.dispatch_ops import _shared as dispatch_shared

_FIXED_OCR: dict[str, Any] = {
    "pages": 1,
    "text": "ROUTE OCR TEXT",
    "per_page": [{"page": 1, "text": "ROUTE OCR TEXT", "tokens_used": 50}],
    "model": "openai/gpt-5.4",
    "total_tokens": 50,
}


def _write_minimal_png(path: Path) -> None:
    import struct
    import zlib

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00" + b"\xff\xff\xff"
    idat = zlib.compress(raw)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )
    path.write_bytes(png)


@pytest.fixture()
def ocr_client(
    tmp_path: Path,
    migrated_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, Path]:
    files_root = tmp_path / "files"
    files_root.mkdir()
    monkeypatch.setattr(dispatch_shared, "_FILES_ROOT", files_root)
    monkeypatch.setattr("cortex_store.routes.documents._FILES_ROOT", files_root)
    from cortex_store.main import create_app

    return TestClient(create_app(db_path=str(migrated_db_path))), files_root


@patch("cortex_store.routes.documents.ocr_pages", return_value=_FIXED_OCR)
def test_ocr_file_endpoint_smoke(
    mock_ocr_pages: Any,
    ocr_client: tuple[TestClient, Path],
) -> None:
    client, files_root = ocr_client
    sample = files_root / "scan.png"
    _write_minimal_png(sample)

    resp = client.post(
        "/documents/ocr/file",
        json={"path": "scan.png"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "ROUTE OCR TEXT"
    assert body["path"] == "scan.png"
    mock_ocr_pages.assert_called_once()


@patch("cortex_store.routes.documents.ocr_directory")
def test_ocr_directory_endpoint_smoke(
    mock_ocr_directory: Any,
    ocr_client: tuple[TestClient, Path],
) -> None:
    client, files_root = ocr_client
    batch_dir = files_root / "batch"
    batch_dir.mkdir()
    _write_minimal_png(batch_dir / "a.png")

    mock_ocr_directory.return_value = {
        "results": [{"path": "batch/a.png", "text": "ROUTE OCR TEXT", "pages": 1}],
        "errors": [],
        "summary": {"total_files": 1, "successful": 1, "failed": 0},
    }

    resp = client.post(
        "/documents/ocr/directory",
        json={"directory": "batch"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["successful"] == 1
    assert body["results"][0]["text"] == "ROUTE OCR TEXT"
    mock_ocr_directory.assert_called_once()


def test_stargate_chat_importable_for_cortex_api_runtime() -> None:
    import ocr_core
    import stargate_chat

    assert callable(stargate_chat.call_stargate)
    assert callable(ocr_core.ocr_pages)
