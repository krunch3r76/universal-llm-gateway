"""Regression tests for ``ocr_pages`` after stargate transport relocation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from ocr_core import ocr_pages

_FIXED_RESPONSE: dict[str, Any] = {
    "choices": [{"message": {"content": "FIXED OCR TEXT"}}],
    "model": "openai/gpt-5.4",
    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
}


def _write_minimal_png(path: Path) -> None:
    """Write a 1x1 PNG without external fixtures."""
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


@patch("ocr_core._core.call_stargate", return_value=_FIXED_RESPONSE)
def test_ocr_pages_image_regression(mock_call: Any, tmp_path: Path) -> None:
    image_path = tmp_path / "sample.png"
    _write_minimal_png(image_path)

    result = ocr_pages(image_path, stargate_url="http://stargate:9999")

    assert result == {
        "pages": 1,
        "text": "FIXED OCR TEXT",
        "per_page": [{"page": 1, "text": "FIXED OCR TEXT", "tokens_used": 120}],
        "model": "openai/gpt-5.4",
        "total_tokens": 120,
    }
    mock_call.assert_called_once()
    assert mock_call.call_args.args[0] == "http://stargate:9999"


@patch("ocr_core._core.call_stargate", return_value=_FIXED_RESPONSE)
def test_ocr_pages_error_response_shape(mock_call: Any, tmp_path: Path) -> None:
    mock_call.return_value = {"error": "Stargate timeout"}
    image_path = tmp_path / "sample.png"
    _write_minimal_png(image_path)

    result = ocr_pages(image_path, stargate_url="http://stargate:9999")

    assert result["text"] == "[OCR error: Stargate timeout]"
    assert result["pages"] == 1
