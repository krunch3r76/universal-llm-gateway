"""Fail-closed scanned OCR and the S2 extract/promote runners.

Breaks when a scanned extraction writes a sidecar whose body is an OCR
error, when a text-layer or page-gate extract is refused for the same
literal, or when ``run_promote_document`` converts ``PromoteError``.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("pymupdf")
import pymupdf  # type: ignore[import-untyped]

from tools import _evidence_entity_ops as evidence_entity_ops
from tools import _extraction_profile as extraction_profile
from tools import _promote_document_helpers as promote_helpers
from tools import _sidecar_schema as sidecar_schema
from tools import extract_document as extract_mod
from tools._extract_document_helpers import format_sidecar
from tools._promote_document_helpers import PromoteError
from tools._sidecar_schema import SIDECAR_SUFFIX
from tools.extract_document import (
    OcrProviderError,
    OcrUnreachable,
    register_extract_document_tools,
    run_extract_document,
)
from tools.promote_document_to_evidence import (
    register_promote_document_to_evidence_tools,
    run_promote_document,
)

_SCHEMA = (
    Path(__file__).resolve().parents[1] / "testdata" / "extraction-sidecar-v1.yaml"
)
_PROFILE = """\
profile: document-extraction-v1
model: openai/gpt-5.4
dpi: 200
prompt: |
  Extract all text faithfully.
extraction_type: ocr_transcription
"""
_OTHER_HASH = "0" * 64


class _ToolRecorder:
    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}

    def tool(self, **_kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.registered[fn.__name__] = fn
            return fn

        return decorator


def _png() -> bytes:
    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 8 for _ in range(8))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


def _blank_pdf(path: Path, pages: int) -> None:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()


def _text_pdf(path: Path, text: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def _mixed_pdf(path: Path) -> None:
    """Page 1 text-rich; page 2 thin text plus an image (page-gate OCR)."""
    doc = pymupdf.open()
    page1 = doc.new_page()
    page1.insert_text(
        (72, 72),
        "This is a substantial text layer with enough alphanumeric content "
        "to clear the thin-page threshold for digital extraction.",
    )
    page2 = doc.new_page()
    img = path.with_suffix(".png")
    img.write_bytes(_png())
    page2.insert_image(pymupdf.Rect(72, 72, 200, 200), filename=str(img))
    page2.insert_text((72, 40), "x")
    doc.save(str(path))
    doc.close()


def _place(root: Path, rel: str, writer) -> str:
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    writer(dest)
    return rel


def _sidecars(root: Path) -> list[Path]:
    return list(root.rglob("*.extracted.md"))


def _stargate_error(message: str) -> dict[str, str]:
    return {"error": message}


def _stargate_ok(text: str) -> dict[str, Any]:
    return {
        "model": "openai/gpt-5.4",
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        "choices": [{"message": {"content": text}}],
    }


@pytest.fixture(autouse=True)
def _files_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    schema_dir = tmp_path / "configs" / "schemas"
    schema_dir.mkdir(parents=True)
    (schema_dir / "extraction-sidecar-v1.yaml").write_text(
        _SCHEMA.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "configs" / "document-extraction-v1.yaml").write_text(
        _PROFILE,
        encoding="utf-8",
    )
    from tools import _file_helpers as file_helpers

    for mod in (
        extract_mod,
        file_helpers,
        extraction_profile,
        sidecar_schema,
        promote_helpers,
    ):
        monkeypatch.setattr(mod, "FILES_ROOT", tmp_path)
    extraction_profile.load_default_profile.cache_clear()
    sidecar_schema.load_schema.cache_clear()
    yield tmp_path
    extraction_profile.load_default_profile.cache_clear()
    sidecar_schema.load_schema.cache_clear()


@pytest.mark.parametrize(
    "message",
    [
        "Stargate connection failed",
        "Stargate timeout",
        "Upstream error (503)",
        "Upstream error (429)",
        "Upstream error (500)",
        "Upstream error (502)",
        "Upstream error (504)",
        "Upstream error (599)",
    ],
)
def test_scanned_unreachable_writes_nothing(tmp_path: Path, message: str) -> None:
    rel = _place(tmp_path, "dropbox/finance/scan.pdf", lambda p: _blank_pdf(p, 1))
    with (
        patch("ocr_core._core.call_stargate", return_value=_stargate_error(message)),
        pytest.raises(OcrUnreachable) as exc_info,
    ):
        run_extract_document(rel, dpi=72)
    assert message in str(exc_info.value)
    assert _sidecars(tmp_path) == []


@pytest.mark.parametrize(
    "message",
    [
        "Upstream error (400)",
        "Stargate returned invalid JSON",
        "Stargate returned non-object JSON",
    ],
)
def test_scanned_provider_error_writes_nothing(tmp_path: Path, message: str) -> None:
    rel = _place(tmp_path, "dropbox/finance/scan.pdf", lambda p: _blank_pdf(p, 1))
    with (
        patch("ocr_core._core.call_stargate", return_value=_stargate_error(message)),
        pytest.raises(OcrProviderError) as exc_info,
    ):
        run_extract_document(rel, dpi=72)
    assert message in str(exc_info.value)
    assert _sidecars(tmp_path) == []


def test_second_batch_error_writes_nothing(tmp_path: Path) -> None:
    """Five pages is two vision batches; a batch-2 error leaves no sidecar."""
    rel = _place(tmp_path, "dropbox/finance/scan.pdf", lambda p: _blank_pdf(p, 5))
    replies = [
        _stargate_ok("first batch text"),
        _stargate_error("Upstream error (400)"),
    ]
    with (
        patch("ocr_core._core.call_stargate", side_effect=replies),
        pytest.raises(OcrProviderError),
    ):
        run_extract_document(rel, dpi=72)
    assert _sidecars(tmp_path) == []


def test_unreachable_wins_over_provider_error(tmp_path: Path) -> None:
    rel = _place(tmp_path, "dropbox/finance/scan.pdf", lambda p: _blank_pdf(p, 5))
    replies = [
        _stargate_error("Upstream error (400)"),
        _stargate_error("Stargate timeout"),
    ]
    with (
        patch("ocr_core._core.call_stargate", side_effect=replies),
        pytest.raises(OcrUnreachable) as exc_info,
    ):
        run_extract_document(rel, dpi=72)
    msg = str(exc_info.value)
    assert "Stargate timeout" in msg
    assert "Upstream error (400)" not in msg
    assert _sidecars(tmp_path) == []


def test_scanned_clean_body_writes_sidecar(tmp_path: Path) -> None:
    rel = _place(tmp_path, "dropbox/finance/scan.pdf", lambda p: _blank_pdf(p, 1))
    with patch(
        "ocr_core._core.call_stargate",
        return_value=_stargate_ok("Notice 14052781"),
    ):
        run_extract_document(rel, dpi=72)
    sidecars = _sidecars(tmp_path)
    assert len(sidecars) == 1
    body = sidecars[0].read_text(encoding="utf-8")
    assert "Notice 14052781" in body
    assert "[OCR error: " not in body


class _FakeStargateResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeStargateClient:
    def __init__(self, status_code: int) -> None:
        self._status_code = status_code

    def __enter__(self) -> _FakeStargateClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(self, *_args: object, **_kwargs: object) -> _FakeStargateResponse:
        return _FakeStargateResponse(self._status_code, "bad gateway")


def test_real_call_stargate_502_is_unreachable(tmp_path: Path) -> None:
    rel = _place(tmp_path, "dropbox/finance/scan.pdf", lambda p: _blank_pdf(p, 1))

    def _client_factory(*_args: object, **_kwargs: object) -> _FakeStargateClient:
        return _FakeStargateClient(502)

    with (
        patch("stargate_chat.make_sync_client", side_effect=_client_factory),
        pytest.raises(OcrUnreachable) as exc_info,
    ):
        run_extract_document(rel, dpi=72)
    assert "Upstream error (502)" in str(exc_info.value)
    assert _sidecars(tmp_path) == []


def test_run_promote_directory_is_plain_value_error(tmp_path: Path) -> None:
    rel = "dropbox/legal/folder"
    dest = tmp_path / rel
    dest.mkdir(parents=True)
    with pytest.raises(ValueError, match=r"^Not a file: ") as exc_info:
        run_promote_document(rel, "document:folder", "desc")
    assert type(exc_info.value) is ValueError
    assert not isinstance(exc_info.value, PromoteError)


def test_text_pdf_literal_is_not_refused(tmp_path: Path) -> None:
    body = "Notice text that literally contains [OCR error: Stargate timeout] inline."
    rel = _place(tmp_path, "dropbox/finance/letter.pdf", lambda p: _text_pdf(p, body))
    result = run_extract_document(rel)
    assert result["sidecar_path"]
    written = _sidecars(tmp_path)
    assert len(written) == 1
    assert "[OCR error: " in written[0].read_text(encoding="utf-8")


def test_text_pdf_page_gate_ocr_error_still_writes(tmp_path: Path) -> None:
    rel = _place(tmp_path, "dropbox/finance/mixed.pdf", _mixed_pdf)
    with patch(
        "ocr_core._core.call_stargate",
        return_value=_stargate_error("Stargate timeout"),
    ):
        result = run_extract_document(rel, dpi=72, pages=[1, 2])
    assert result["sidecar_path"]
    text = _sidecars(tmp_path)[0].read_text(encoding="utf-8")
    assert "[OCR error: Stargate timeout]" in text


def test_tool_surfaces_ocr_unreachable(tmp_path: Path) -> None:
    rel = _place(tmp_path, "dropbox/finance/scan.pdf", lambda p: _blank_pdf(p, 1))
    recorder = _ToolRecorder()
    register_extract_document_tools(recorder)
    tool = recorder.registered["extract_document"]
    with (
        patch(
            "ocr_core._core.call_stargate",
            return_value=_stargate_error("Stargate connection failed"),
        ),
        pytest.raises(OcrUnreachable),
    ):
        tool(rel, dpi=72)
    assert _sidecars(tmp_path) == []


def _stage_promote_source(root: Path) -> str:
    rel = "dropbox/legal/bill.pdf"
    payload = b"%PDF-1.4 test source for promote\n"
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    import hashlib

    sha = hashlib.sha256(payload).hexdigest()
    frontmatter = {
        "naming_version": 1,
        "canonical": True,
        "partial": False,
        "page_spec": "all",
        "args_hash": None,
        "args_hash_prefix": None,
        "default_profile": "document-extraction-v1",
        "source_path": rel,
        "source_sha256": sha,
        "source_size": len(payload),
        "extracted_at": "2026-05-23T20:00:00Z",
        "model": "openai/gpt-5.4",
        "dpi": 200,
        "pages": "all",
        "prompt_hash": "ab" * 32,
        "extraction_type": "ocr_transcription",
        "tool_version": "extract_document/1.0",
    }
    sidecar = dest.parent / f"{dest.name}{SIDECAR_SUFFIX}"
    sidecar.write_text(format_sidecar(frontmatter, "OCR text"), encoding="utf-8")
    return rel


def _conflict_cx(_method: str, _path: str, body: dict[str, Any]) -> dict[str, Any]:
    assert body["tool"] == "entity_get"
    return {
        "id": body["arguments"]["entity_id"],
        "type": "document",
        "content_hash": _OTHER_HASH,
    }


def test_run_promote_entity_conflict_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rel = _stage_promote_source(tmp_path)
    monkeypatch.setattr(evidence_entity_ops, "cx", _conflict_cx)
    with pytest.raises(PromoteError) as exc_info:
        run_promote_document(
            rel,
            "document:bill",
            "desc",
        )
    assert exc_info.value.code == "entity_conflict"
    assert type(exc_info.value) is PromoteError
    assert (tmp_path / rel).is_file()


def test_tool_wrapper_converts_entity_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rel = _stage_promote_source(tmp_path)
    monkeypatch.setattr(evidence_entity_ops, "cx", _conflict_cx)
    recorder = _ToolRecorder()
    register_promote_document_to_evidence_tools(recorder)
    tool = recorder.registered["promote_document_to_evidence"]
    with pytest.raises(ValueError, match=r"^entity_conflict: ") as exc_info:
        tool(rel, "document:bill", "desc")
    assert not isinstance(exc_info.value, PromoteError)
    assert (tmp_path / rel).is_file()
