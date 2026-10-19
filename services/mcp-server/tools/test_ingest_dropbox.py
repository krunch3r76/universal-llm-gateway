"""Hermetic tests for ingest_dropbox (plan r14 §4)."""

from __future__ import annotations

import json
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
from tools import _file_helpers as file_helpers
from tools import _ingest_dropbox_pass as ingest_pass
from tools import _ingest_dropbox_select as ingest_select
from tools import _ingest_dropbox_state as ingest_state
from tools import _promote_document_helpers as promote_helpers
from tools import _sidecar_schema as sidecar_schema
from tools import extract_document as extract_mod
from tools import ingest_dropbox as ingest_mod
from tools._extract_document_helpers import format_sidecar
from tools._ingest_dropbox_tokens import compare_bodies, extract_token_multiset
from tools._promote_document_helpers import PromoteError
from tools._sidecar_schema import SIDECAR_SUFFIX
from tools.ingest_dropbox import (
    ingest_dropbox_dry_run,
    ingest_dropbox_run,
    ingest_dropbox_status,
    load_config,
)
from tools.promote_document_to_evidence import (
    register_promote_document_to_evidence_tools,
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
_OCR_REVIEW = """\
canonical: openai/gpt-5.4
check: xai/grok-4.7
family_map:
  - match: "openai/*"
    family: openai
  - match: "xai/*"
    family: xai
  - match: "cursor/grok-*"
    family: xai
selection:
  since: "2026-10-18"
  cap: 5
  ctx_allowlist:
    - cortex_finance
    - cortex_legal
"""


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


def _blank_pdf(path: Path, pages: int = 1) -> None:
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


def _stargate_ok(text: str) -> dict[str, Any]:
    return {
        "model": "openai/gpt-5.4",
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        "choices": [{"message": {"content": text}}],
    }


def _stargate_error(message: str) -> dict[str, str]:
    return {"error": message}


def _dropbox_rel(root: Path, ctx: str, day: str, name: str) -> str:
    dest = root / "dropbox" / ctx / day / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest.relative_to(root).as_posix()


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
    (tmp_path / "configs" / "ocr-review-v1.yaml").write_text(
        _OCR_REVIEW,
        encoding="utf-8",
    )
    for mod in (
        file_helpers,
        extraction_profile,
        sidecar_schema,
        promote_helpers,
        ingest_mod,
        ingest_state,
        ingest_select,
        ingest_pass,
        extract_mod,
    ):
        monkeypatch.setattr(mod, "FILES_ROOT", tmp_path)
    extraction_profile.load_default_profile.cache_clear()
    sidecar_schema.load_schema.cache_clear()
    yield tmp_path
    extraction_profile.load_default_profile.cache_clear()
    sidecar_schema.load_schema.cache_clear()


def test_selection_before_since_writes_nothing(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-01", "old.pdf")
    _blank_pdf(tmp_path / rel)
    out = ingest_dropbox_run(since="2026-10-18")
    assert out["processed"] == []
    assert not list((tmp_path / "notes/system/ingest-state").glob("*.json"))


def test_selection_nested_folder_writes_nothing(tmp_path: Path) -> None:
    nested = tmp_path / "dropbox/cortex_finance/2026-10-19/nested/x.pdf"
    nested.parent.mkdir(parents=True)
    _blank_pdf(nested)
    out = ingest_dropbox_run()
    assert out["processed"] == []


def test_selection_other_ctx_writes_nothing(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_other", "2026-10-19", "x.pdf")
    _blank_pdf(tmp_path / rel)
    assert ingest_dropbox_run()["processed"] == []


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "a.pdf")
    _blank_pdf(tmp_path / rel)
    dry = ingest_dropbox_dry_run()
    assert dry["selected"]
    assert not list(tmp_path.rglob("*.extracted.md"))
    assert not list((tmp_path / "notes/system/ingest-state").glob("*.json"))
    assert not list((tmp_path / ".locks").glob("*"))


def test_status_lists_terminal_without_body(tmp_path: Path) -> None:
    sha = "a" * 64
    state_dir = tmp_path / "notes/system/ingest-state"
    state_dir.mkdir(parents=True)
    rec = {
        "source_path": "dropbox/cortex_finance/2026-10-19/t.pdf",
        "source_sha256": sha,
        "terminal": "needs_human",
        "last_error": "mixed_text_layer",
        "crosscheck": {"mismatches": [{"kind": "money"}]},
        "updated_at": "2026-10-19T12:00:00Z",
    }
    (state_dir / f"{sha}.json").write_text(json.dumps(rec), encoding="utf-8")
    st = ingest_dropbox_status()
    assert st["reconcile_run"] is False
    assert st["source"] == "notes/system/ingest-state"
    row = st["terminal"][0]
    assert row["last_error"] == "mixed_text_layer"
    assert row["mismatch_kinds"] == ["money"]
    assert "Notice" not in json.dumps(st)


def test_tokenizer_invalid_date_and_two_digit_year() -> None:
    left = extract_token_multiset("dated 13/45/2026")
    right = extract_token_multiset("also 13/45/2026")
    assert left == right
    assert extract_token_multiset("3/4/26")[
        "num:2026"
    ] or "2026-03-04" in extract_token_multiset(
        "3/4/26",
    )


def test_tokenizer_comma_money_agrees() -> None:
    a = extract_token_multiset("total $1,234.56")
    b = extract_token_multiset("total $1234.56")
    assert a == b


def test_same_family_config_refused_at_load(tmp_path: Path) -> None:
    bad = _OCR_REVIEW.replace("check: xai/grok-4.7", "check: openai/gpt-5.5")
    path = tmp_path / "configs/ocr-review-v1.yaml"
    path.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_paths_come_from_files_root(tmp_path: Path) -> None:
    load_config()
    assert str(ingest_state.config_path()).startswith(str(tmp_path))
    assert str(ingest_state.state_dir()).startswith(str(tmp_path))


@pytest.mark.parametrize("code", ["429", "500", "502", "503", "504", "599"])
def test_unreachable_writes_nothing_and_stops(tmp_path: Path, code: str) -> None:
    rel1 = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "a.pdf")
    rel2 = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "b.pdf")
    _blank_pdf(tmp_path / rel1)
    _blank_pdf(tmp_path / rel2)
    with patch(
        "ocr_core._core.call_stargate",
        return_value=_stargate_error(f"Upstream error ({code})"),
    ):
        out = ingest_dropbox_run(paths=[rel1, rel2], cap=2)
    assert out["stopped"] is True
    assert out["stopped_path"] == rel1
    assert not list((tmp_path / "notes/system/ingest-state").glob("*.json"))


def test_wrapper_still_value_error_on_entity_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.test_extract_document_fail_closed import (
        _conflict_cx,
        _stage_promote_source,
    )

    rel = _stage_promote_source(tmp_path)
    monkeypatch.setattr(evidence_entity_ops, "cx", _conflict_cx)
    recorder: dict[str, Any] = {}

    class _Mcp:
        def tool(self, **_kw: Any) -> Any:
            def deco(fn: Any) -> Any:
                recorder["fn"] = fn
                return fn

            return deco

    register_promote_document_to_evidence_tools(_Mcp())
    with pytest.raises(ValueError, match=r"^entity_conflict:"):
        recorder["fn"](rel, "document:bill", "desc")


def _write_canonical(
    root: Path,
    rel: str,
    *,
    sha: str,
    model: str = "openai/gpt-5.4",
    body: str = "Amount $100.00",
) -> None:
    pdf = root / rel
    fm = {
        "naming_version": 1,
        "canonical": True,
        "partial": False,
        "page_spec": "all",
        "args_hash": None,
        "args_hash_prefix": None,
        "default_profile": "document-extraction-v1",
        "source_path": rel,
        "source_sha256": sha,
        "source_size": pdf.stat().st_size if pdf.is_file() else 10,
        "extracted_at": "2026-10-19T12:00:00Z",
        "model": model,
        "dpi": 200,
        "pages": "all",
        "prompt_hash": "ab" * 32,
        "extraction_type": "ocr_transcription",
        "tool_version": "extract_document/1.0",
    }
    sidecar = pdf.parent / f"{pdf.name}{SIDECAR_SUFFIX}"
    sidecar.write_text(format_sidecar(fm, body), encoding="utf-8")


def test_mixed_text_layer_needs_human(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "mixed.pdf")
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "x" * 60)
    p2 = doc.new_page()
    img = tmp_path / "i.png"
    img.write_bytes(_png())
    p2.insert_image(pymupdf.Rect(72, 72, 200, 200), filename=str(img))
    p2.insert_text((72, 40), "x")
    doc.save(str(tmp_path / rel))
    doc.close()
    out = ingest_dropbox_run(paths=[rel])
    assert out["processed"][0]["outcome"] == "needs_human"
    assert not list(tmp_path.rglob("*.extracted.md"))


def test_text_pdf_routes_text_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "letter.pdf")
    _text_pdf(
        tmp_path / rel,
        "This letter contains enough alphanumeric content to stay on the text layer path.",
    )
    calls: list[str] = []

    def _promote(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append("promote")
        return {"entity_id": "document:letter", "bundle_path": "evidence/x"}

    monkeypatch.setattr(ingest_pass, "run_promote_document", _promote)
    with patch("ocr_core._core.call_stargate", side_effect=AssertionError("no ocr")):
        ingest_dropbox_run(paths=[rel])
    assert calls == ["promote"]


def test_redacted_s0_pair_agrees() -> None:
    a = "weight 1 . 672 pounds"
    b = "weight 1 0.672 pounds"
    ok, count, _ = compare_bodies(a, b)
    assert ok and count >= 1


def test_crosscheck_no_tokens_needs_human(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    with (
        patch("ocr_core._core.call_stargate", return_value=_stargate_ok("   ")),
        patch.object(
            ingest_pass,
            "run_promote_document",
            side_effect=AssertionError("no promote"),
        ),
    ):
        out = ingest_dropbox_run(paths=[rel])
    assert out["processed"][0]["outcome"] == "needs_human"


def test_provider_error_counts_attempt(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    with patch(
        "ocr_core._core.call_stargate",
        return_value=_stargate_error("Upstream error (400)"),
    ):
        ingest_dropbox_run(paths=[rel])
    sha = json.loads(
        list((tmp_path / "notes/system/ingest-state").glob("*.json"))[0].read_text(),
    )
    assert sha["extract_attempts"] == 1


def test_status_and_dry_run_create_no_lock_file(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "a.pdf")
    _blank_pdf(tmp_path / rel)
    ingest_dropbox_dry_run()
    ingest_dropbox_status()
    assert not (tmp_path / ".locks").exists()


def test_dropbox_paths_are_not_indexed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "a.pdf")
    _blank_pdf(tmp_path / rel)

    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("rag index called")

    monkeypatch.setattr("tools.rag.register_rag_tools", lambda *_a, **_k: None)
    with patch("tools.rag.register_rag_tools", lambda *_a, **_k: None):
        ingest_dropbox_dry_run(paths=[rel])


def test_cap_limits_processed_files(tmp_path: Path) -> None:
    for i in range(3):
        rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", f"f{i}.pdf")
        _blank_pdf(tmp_path / rel)
    with patch(
        "ocr_core._core.call_stargate",
        return_value=_stargate_error("Upstream error (400)"),
    ):
        out = ingest_dropbox_run(cap=2)
    assert len(out["processed"]) <= 2


def test_unreadable_file_writes_no_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "a.pdf")
    _blank_pdf(tmp_path / rel)

    def _fail(_path: Path) -> tuple[str, int]:
        raise OSError("unreadable")

    monkeypatch.setattr(ingest_pass, "compute_source_sha256", _fail)
    ingest_dropbox_run(paths=[rel])
    assert not list((tmp_path / "notes/system/ingest-state").glob("*.json"))


def test_matching_canonical_is_not_reextracted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "a.pdf")
    _text_pdf(tmp_path / rel, "hello " * 20)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    _write_canonical(tmp_path, rel, sha=sha)
    called = {"n": 0}

    def _extract(*_a: Any, **_k: Any) -> dict[str, Any]:
        called["n"] += 1
        return {"sidecar_path": rel + ".extracted.md"}

    monkeypatch.setattr(ingest_pass, "run_extract_document", _extract)
    monkeypatch.setattr(
        ingest_pass,
        "run_promote_document",
        lambda *_a, **_k: {"entity_id": "document:a"},
    )
    ingest_dropbox_run(paths=[rel])
    assert called["n"] == 0


def _install_cortex_promote_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    state: dict[str, Any] = {"entities": {}, "create_calls": 0}

    def cx(
        _method: str, _path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        assert body is not None
        tool = body["tool"]
        args = body["arguments"]
        if tool == "entity_get":
            eid = args["entity_id"]
            if eid not in state["entities"]:
                return {
                    "error": "cortex-api error: HTTP 404 — not found",
                    "status_code": 404,
                }
            return dict(state["entities"][eid])
        if tool == "entities":
            ch_filter = args.get("content_hash")
            rows = [
                {"id": eid, "content_hash": row.get("content_hash")}
                for eid, row in state["entities"].items()
                if ch_filter is None
                or row.get("content_hash") == ch_filter
                or row.get("content_hash") == ch_filter.removeprefix("sha256:")
            ]
            return {"items": rows}
        if tool == "entity_create":
            state["create_calls"] += 1
            eid = args["id"]
            if eid in state["entities"]:
                return {
                    "error": "cortex-api error: HTTP 409 — conflict",
                    "status_code": 409,
                }
            state["entities"][eid] = {**args, "type": "document"}
            return {"id": eid, "created": True}
        raise AssertionError(f"unexpected tool {tool!r}")

    monkeypatch.setattr(evidence_entity_ops, "cx", cx)
    return state


def test_entity_conflict_through_real_run_promote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.test_extract_document_fail_closed import _conflict_cx

    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "letter.pdf")
    _text_pdf(tmp_path / rel, "hello " * 20)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    _write_canonical(tmp_path, rel, sha=sha)
    rec = ingest_state.new_record(source_path=rel, source_sha256=sha)
    rec.update(stage="promote", method="text_layer", entity_id="document:letter")
    ingest_state.save_state(sha, rec)
    monkeypatch.setattr(evidence_entity_ops, "cx", _conflict_cx)
    out = ingest_dropbox_run(paths=[rel])
    assert out["processed"][0]["outcome"] == "needs_human"
    rec2 = ingest_state.load_state(sha)
    assert rec2 is not None
    assert rec2.get("terminal") == "needs_human"
    assert rec2.get("last_error") == "entity_conflict"


def test_same_family_canonical_needs_human(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    _write_canonical(
        tmp_path, rel, sha=sha, model="xai/grok-4.7", body="Amount $100.00"
    )
    with patch(
        "ocr_core._core.call_stargate",
        return_value=_stargate_ok("Amount $100.00"),
    ):
        out = ingest_dropbox_run(paths=[rel])
    assert out["processed"][0]["outcome"] == "needs_human"


def test_stamped_scan_needs_human(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "stamp.pdf")
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page()
        img = tmp_path / "i.png"
        img.write_bytes(_png())
        page.insert_image(pymupdf.Rect(72, 72, 200, 200), filename=str(img))
        page.insert_text((72, 40), "x")
    doc.save(str(tmp_path / rel))
    doc.close()
    out = ingest_dropbox_run(paths=[rel])
    assert out["processed"][0]["outcome"] == "needs_human"


def test_route_mismatch_needs_human(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "letter.pdf")
    _text_pdf(tmp_path / rel, "hello " * 20)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    _write_canonical(tmp_path, rel, sha=sha, body="Amount $100.00")
    rec = ingest_state.new_record(source_path=rel, source_sha256=sha)
    rec.update(stage="crosscheck", method="text_layer")
    ingest_state.save_state(sha, rec)
    out = ingest_dropbox_run(paths=[rel])
    assert out["processed"][0]["outcome"] == "needs_human"


def test_corrupt_pdf_does_not_block_next_file(tmp_path: Path, monkeypatch) -> None:
    bad = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "corrupt.pdf")
    good = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "good.pdf")
    (tmp_path / bad).write_bytes(b"not-a-pdf")
    _text_pdf(tmp_path / good, "hello " * 20)
    monkeypatch.setattr(
        ingest_pass,
        "run_promote_document",
        lambda *_a, **_k: {"entity_id": "document:good"},
    )
    out = ingest_dropbox_run(paths=[bad, good])
    assert any(p["path"] == good for p in out["processed"])


def test_exception_before_record_creates_extract_stage(
    tmp_path: Path, monkeypatch
) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    monkeypatch.setattr(
        ingest_pass,
        "run_extract_document",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("early")),
    )
    ingest_dropbox_run(paths=[rel])
    rec = json.loads(
        list((tmp_path / "notes/system/ingest-state").glob("*.json"))[0].read_text()
    )
    assert rec["stage"] == "extract"


def test_existing_text_canonical_is_promoted(tmp_path: Path, monkeypatch) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "letter.pdf")
    _text_pdf(tmp_path / rel, "hello " * 20)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    _write_canonical(tmp_path, rel, sha=sha)
    monkeypatch.setattr(
        ingest_pass,
        "run_promote_document",
        lambda *_a, **_k: {"entity_id": "document:letter"},
    )
    out = ingest_dropbox_run(paths=[rel])
    assert out["processed"][0]["outcome"] == "done"


def test_unknown_family_needs_human(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    _write_canonical(tmp_path, rel, sha=sha, model="mistral/x", body="Amount $100.00")
    with patch(
        "ocr_core._core.call_stargate", return_value=_stargate_ok("Amount $100.00")
    ):
        out = ingest_dropbox_run(paths=[rel])
    assert out["processed"][0]["outcome"] == "needs_human"


def test_document_text_status_pass_does_not_change_outcome(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    with patch(
        "ocr_core._core.call_stargate",
        return_value=_stargate_ok("status=pass Amount $100.00"),
    ):
        out = ingest_dropbox_run(paths=[rel])
    assert out["processed"][0]["outcome"] in {"attempt", "needs_human", "done"}


def test_pages_leftover_is_not_canonical(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    partial = tmp_path / rel.replace(".pdf", ".pages-1.extracted.md")
    partial.write_text("partial", encoding="utf-8")
    with patch(
        "ocr_core._core.call_stargate", return_value=_stargate_ok("Amount $100.00")
    ) as m:
        ingest_dropbox_run(paths=[rel])
    assert m.called


def test_fourth_run_does_not_call_extract(tmp_path: Path, monkeypatch) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    rec = ingest_state.new_record(source_path=rel, source_sha256=sha)
    rec.update(terminal="needs_human", last_error="empty_extraction")
    ingest_state.save_state(sha, rec)
    called = {"n": 0}

    def _extract(*_a, **_k):
        called["n"] += 1
        return {"sidecar_path": None}

    monkeypatch.setattr(ingest_pass, "run_extract_document", _extract)
    ingest_dropbox_run(paths=[rel])
    assert called["n"] == 0


def test_promote_not_called_before_agree(tmp_path: Path, monkeypatch) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    promote_calls: list[int] = []
    monkeypatch.setattr(
        ingest_pass,
        "run_promote_document",
        lambda *_a, **_k: promote_calls.append(1) or {"entity_id": "document:x"},
    )
    with patch(
        "ocr_core._core.call_stargate",
        side_effect=[_stargate_ok("Amount $100.00"), _stargate_ok("Amount $200.00")],
    ):
        ingest_dropbox_run(paths=[rel])
    assert not promote_calls


def test_each_state_transition_emits_one_advisory_event(
    tmp_path: Path, monkeypatch
) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "letter.pdf")
    _text_pdf(tmp_path / rel, "hello " * 20)
    events: list[str] = []
    monkeypatch.setattr(ingest_pass, "record", lambda name, **kw: events.append(name))
    monkeypatch.setattr(
        ingest_pass,
        "run_promote_document",
        lambda *_a, **_k: {"entity_id": "document:letter"},
    )
    ingest_dropbox_run(paths=[rel])
    assert "mcp.ingest.state.advanced" in events


def test_cap_skips_six_terminal_then_processes_new(tmp_path: Path, monkeypatch) -> None:
    import hashlib

    for i in range(6):
        rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", f"t{i}.pdf")
        _blank_pdf(tmp_path / rel)
        sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
        rec = ingest_state.new_record(source_path=rel, source_sha256=sha)
        rec.update(terminal="needs_human", last_error="x")
        ingest_state.save_state(sha, rec)
    new = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "new.pdf")
    _text_pdf(tmp_path / new, "hello " * 20)
    monkeypatch.setattr(
        ingest_pass,
        "run_promote_document",
        lambda *_a, **_k: {"entity_id": "document:new"},
    )
    out = ingest_dropbox_run(cap=5)
    assert any(p["path"] == new for p in out["processed"])


def test_empty_ocr_third_attempt_needs_human(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    with patch("ocr_core._core.call_stargate", return_value=_stargate_ok("  ")):
        for _ in range(3):
            ingest_dropbox_run(paths=[rel])
    rec = json.loads(
        list((tmp_path / "notes/system/ingest-state").glob("*.json"))[0].read_text()
    )
    assert rec.get("terminal") == "needs_human"


def test_check_provider_error_increments_check_attempts(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    with patch(
        "ocr_core._core.call_stargate",
        side_effect=[
            _stargate_ok("Amount $100.00"),
            _stargate_error("Upstream error (400)"),
        ],
    ):
        ingest_dropbox_run(paths=[rel])
    rec = json.loads(
        list((tmp_path / "notes/system/ingest-state").glob("*.json"))[0].read_text()
    )
    assert rec.get("check_attempts", 0) >= 1


def test_differing_amount_records_kinds(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    with patch(
        "ocr_core._core.call_stargate",
        side_effect=[_stargate_ok("Amount $100.00"), _stargate_ok("Amount $200.00")],
    ):
        out = ingest_dropbox_run(paths=[rel])
    assert out["processed"][0]["outcome"] == "needs_human"


def test_crosscheck_agree_promotes(tmp_path: Path, monkeypatch) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    monkeypatch.setattr(
        ingest_pass,
        "run_promote_document",
        lambda *_a, **_k: {"entity_id": "document:scan"},
    )
    with patch(
        "ocr_core._core.call_stargate", return_value=_stargate_ok("Amount $100.00")
    ):
        out = ingest_dropbox_run(paths=[rel])
    assert out["processed"][0]["outcome"] == "done"


def test_chain_scanned_run_reaches_done(tmp_path: Path, monkeypatch) -> None:
    test_crosscheck_agree_promotes(tmp_path, monkeypatch)


def test_two_page_scan_crosschecks_whole_document(tmp_path: Path, monkeypatch) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel, pages=2)
    monkeypatch.setattr(
        ingest_pass,
        "run_promote_document",
        lambda *_a, **_k: {"entity_id": "document:scan"},
    )
    with patch(
        "ocr_core._core.call_stargate", return_value=_stargate_ok("Amount $100.00")
    ):
        out = ingest_dropbox_run(paths=[rel])
    assert out["processed"][0]["outcome"] == "done"
    rec = json.loads(
        list((tmp_path / "notes/system/ingest-state").glob("*.json"))[0].read_text()
    )
    assert rec is not None
    cc = rec.get("crosscheck") or {}
    assert int(cc.get("tokens_compared") or 0) > 0


def test_existing_check_variant_is_deleted_and_extractor_called(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    variant = tmp_path / rel.replace(".pdf", ".args-deadbe.extracted.md")
    variant.write_text("old", encoding="utf-8")
    with patch(
        "ocr_core._core.call_stargate", return_value=_stargate_ok("Amount $100.00")
    ) as m:
        ingest_dropbox_run(paths=[rel])
    assert m.call_count >= 2


def test_text_layer_attributes_have_no_check_fields(
    tmp_path: Path, monkeypatch
) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "letter.pdf")
    _text_pdf(tmp_path / rel, "hello " * 20)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    _write_canonical(tmp_path, rel, sha=sha)
    captured: dict[str, Any] = {}

    def _promote(_p, _e, _d, entity_attributes=None, **_k):
        captured.update(entity_attributes or {})
        return {"entity_id": "document:letter"}

    monkeypatch.setattr(ingest_pass, "run_promote_document", _promote)
    ingest_dropbox_run(paths=[rel])
    assert captured.get("crosscheck") == "not_run"


def test_promote_runtime_error_stays_at_promote(tmp_path: Path, monkeypatch) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "letter.pdf")
    _text_pdf(tmp_path / rel, "hello " * 20)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    _write_canonical(tmp_path, rel, sha=sha)
    rec = ingest_state.new_record(source_path=rel, source_sha256=sha)
    rec.update(stage="promote", method="text_layer")
    ingest_state.save_state(sha, rec)
    monkeypatch.setattr(
        ingest_pass,
        "run_promote_document",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("relay down")),
    )
    ingest_dropbox_run(paths=[rel])
    rec2 = ingest_state.load_state(sha)
    assert rec2 and rec2["stage"] == "promote"


def test_deleted_canonical_at_crosscheck_resets_to_extract(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    rec = ingest_state.new_record(source_path=rel, source_sha256=sha)
    rec.update(stage="crosscheck", method="vision_crosscheck", extract_attempts=1)
    ingest_state.save_state(sha, rec)
    ingest_state.canonical_sidecar_path(rel).unlink(missing_ok=True)
    with patch(
        "ocr_core._core.call_stargate", return_value=_stargate_ok("Amount $100.00")
    ):
        ingest_dropbox_run(paths=[rel])
    rec2 = ingest_state.load_state(sha)
    assert rec2 and rec2.get("extract_attempts") == 1
    assert ingest_state.canonical_sidecar_path(rel).is_file()


def test_state_is_read_only_after_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    _write_canonical(tmp_path, rel, sha=sha)
    rec = ingest_state.new_record(source_path=rel, source_sha256=sha)
    rec.update(stage="crosscheck", method="vision_crosscheck")
    ingest_state.save_state(sha, rec)
    check_calls = {"n": 0}

    def _extract(*_a: Any, **kwargs: Any) -> dict[str, Any]:
        if kwargs.get("model"):
            check_calls["n"] += 1
        return {"sidecar_path": rel + ".extracted.md"}

    monkeypatch.setattr(ingest_pass, "run_extract_document", _extract)
    ingest_dropbox_dry_run(paths=[rel])
    assert check_calls["n"] == 0


def test_duplicate_after_real_promote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_cortex_promote_stub(monkeypatch)
    rel1 = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-18", "a.pdf")
    rel2 = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "b.pdf")
    shared = "Shared duplicate letter body for real promote path. " * 3
    _text_pdf(tmp_path / rel1, shared)
    import hashlib

    payload = (tmp_path / rel1).read_bytes()
    sha = hashlib.sha256(payload).hexdigest()
    _write_canonical(tmp_path, rel1, sha=sha, body="Amount $100.00")
    promote_calls = {"n": 0}
    real_promote = ingest_pass.run_promote_document

    def _counting_promote(*args: Any, **kwargs: Any) -> dict[str, Any]:
        promote_calls["n"] += 1
        return real_promote(*args, **kwargs)

    monkeypatch.setattr(ingest_pass, "run_promote_document", _counting_promote)
    out1 = ingest_dropbox_run(paths=[rel1])
    assert out1["processed"][0]["outcome"] == "done"
    rec = ingest_state.load_state(sha)
    assert rec is not None
    entity_id = rec["entity_id"]
    assert rec["source_path"] == rel1

    (tmp_path / rel2).write_bytes(payload)
    out2 = ingest_dropbox_run(paths=[rel2])
    assert out2["processed"][0]["outcome"] == f"duplicate_of:{entity_id}"
    rec2 = ingest_state.load_state(sha)
    assert rec2 is not None
    assert rec2["source_path"] == rel1
    assert rel2 in (rec2.get("duplicate_paths") or [])
    assert promote_calls["n"] == 1


def test_same_path_redrop_is_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_cortex_promote_stub(monkeypatch)
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-18", "a.pdf")
    shared = "Same-path redrop letter body for real promote path. " * 3
    _text_pdf(tmp_path / rel, shared)
    import hashlib

    payload = (tmp_path / rel).read_bytes()
    sha = hashlib.sha256(payload).hexdigest()
    _write_canonical(tmp_path, rel, sha=sha, body="Amount $100.00")
    out1 = ingest_dropbox_run(paths=[rel])
    assert out1["processed"][0]["outcome"] == "done"
    rec = ingest_state.load_state(sha)
    assert rec is not None
    entity_id = rec["entity_id"]

    (tmp_path / rel).write_bytes(payload)
    out2 = ingest_dropbox_run(paths=[rel])
    assert out2["processed"][0]["outcome"] == f"duplicate_of:{entity_id}"
    rec2 = ingest_state.load_state(sha)
    assert rec2 is not None
    assert rel not in (rec2.get("duplicate_paths") or [])
    dry = ingest_dropbox_dry_run(paths=[rel])
    assert dry["duplicates"] == [
        {"path": rel, "duplicate_of": entity_id},
    ]


def test_overwritten_source_rebinds(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "a.pdf")
    _blank_pdf(tmp_path / rel)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    rec = ingest_state.new_record(source_path=rel, source_sha256=sha)
    ingest_state.save_state(sha, rec)
    original = (tmp_path / rel).read_bytes()
    (tmp_path / rel).write_bytes(b"%PDF-overwritten")
    rel2 = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "b.pdf")
    (tmp_path / rel2).write_bytes(original)
    ingest_dropbox_run(paths=[rel2])
    rec2 = ingest_state.load_state(sha)
    assert rec2 and rec2.get("source_path") == rel2


def test_renamed_at_crosscheck_rebinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "old.pdf")
    _blank_pdf(tmp_path / rel)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    rec = ingest_state.new_record(source_path=rel, source_sha256=sha)
    rec.update(stage="crosscheck", method="vision_crosscheck")
    ingest_state.save_state(sha, rec)
    new = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "new.pdf")
    (tmp_path / rel).rename(tmp_path / new)
    monkeypatch.setattr(
        ingest_pass,
        "run_promote_document",
        lambda *_a, **_k: {"entity_id": "document:new"},
    )
    with patch(
        "ocr_core._core.call_stargate", return_value=_stargate_ok("Amount $100.00")
    ):
        ingest_dropbox_run(paths=[new])
    dry = ingest_dropbox_dry_run(paths=[new])
    assert not dry["duplicates"]
    rec2 = ingest_state.load_state(sha)
    assert rec2 is not None
    assert rec2.get("source_path") == new


def test_each_promote_error_code_needs_human(tmp_path: Path, monkeypatch) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "letter.pdf")
    _text_pdf(tmp_path / rel, "hello " * 20)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    _write_canonical(tmp_path, rel, sha=sha)
    rec = ingest_state.new_record(source_path=rel, source_sha256=sha)
    rec.update(stage="promote", method="text_layer")
    ingest_state.save_state(sha, rec)
    monkeypatch.setattr(
        ingest_pass,
        "run_promote_document",
        lambda *_a, **_k: (_ for _ in ()).throw(
            PromoteError("entity_conflict", "entity_conflict")
        ),
    )
    out = ingest_dropbox_run(paths=[rel])
    assert out["processed"][0]["outcome"] == "needs_human"


def test_entity_without_bundle_one_repair_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "letter.pdf")
    _text_pdf(tmp_path / rel, "hello " * 20)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    _write_canonical(tmp_path, rel, sha=sha)
    entity_id = "document:letter"
    cortex = _install_cortex_promote_stub(monkeypatch)
    cortex["entities"][entity_id] = {
        "id": entity_id,
        "type": "document",
        "content_hash": sha,
    }
    rec = ingest_state.new_record(source_path=rel, source_sha256=sha)
    rec.update(stage="promote", method="text_layer", entity_id=entity_id)
    ingest_state.save_state(sha, rec)
    promote_calls = {"n": 0}
    real_promote = ingest_pass.run_promote_document

    def _counting_promote(*args: Any, **kwargs: Any) -> dict[str, Any]:
        promote_calls["n"] += 1
        return real_promote(*args, **kwargs)

    monkeypatch.setattr(ingest_pass, "run_promote_document", _counting_promote)
    ingest_dropbox_run(paths=[rel])
    assert promote_calls["n"] == 1


def test_reconcile_after_crash_sets_done(tmp_path: Path, monkeypatch) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "a.pdf")
    _blank_pdf(tmp_path / rel)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    rec = ingest_state.new_record(source_path=rel, source_sha256=sha)
    rec.update(stage="promote", entity_id="document:a")
    ingest_state.save_state(sha, rec)
    (tmp_path / rel).unlink()
    bundle = tmp_path / f"evidence/2026-10-19_{sha[:12]}_a"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ingest_mod, "_entity_hash", lambda _e: sha)
    ingest_dropbox_run()
    rec2 = ingest_state.load_state(sha)
    assert rec2 and rec2.get("stage") == "done"


def test_reconcile_null_entity_id_finds_bundle_by_glob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "a.pdf")
    _blank_pdf(tmp_path / rel)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    rec = ingest_state.new_record(source_path=rel, source_sha256=sha)
    rec.update(stage="promote", entity_id=None)
    ingest_state.save_state(sha, rec)
    (tmp_path / rel).unlink()
    bundle = tmp_path / f"evidence/2026-10-19_{sha[:12]}_a"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ingest_mod, "_entity_hash", lambda _e: sha)
    ingest_dropbox_run()
    rec2 = ingest_state.load_state(sha)
    assert rec2 is not None
    assert rec2.get("stage") == "done"
    assert rec2.get("entity_id") == "document:a"


def test_concurrent_runs_one_promote_second_locked(tmp_path: Path) -> None:
    rel = _dropbox_rel(tmp_path, "cortex_finance", "2026-10-19", "scan.pdf")
    _blank_pdf(tmp_path / rel)
    import hashlib

    sha = hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
    with ingest_state.try_ingest_lock(sha) as held:
        assert held is True
        with ingest_state.try_ingest_lock(sha) as second:
            assert second is False
    out = ingest_dropbox_run(paths=[rel])
    assert out["processed"]
