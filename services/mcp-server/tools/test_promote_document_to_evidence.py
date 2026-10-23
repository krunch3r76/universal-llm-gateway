"""Tests for ``promote_document_to_evidence`` (phase-d).

Hermetic: monkeypatch ``FILES_ROOT`` across promote + extraction helpers and
mock ``cx`` for cortex entity operations.

Spec: cortex://notes/system/specs/document-ingestion-redesign.md
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from tools import _evidence_entity_ops as evidence_entity_ops
from tools import _promote_document_helpers as promote_document_helpers
from tools import _sidecar_schema as sidecar_schema
from tools import promote_document_to_evidence as promote_mod
from tools._extract_document_helpers import format_sidecar
from tools._promote_document_helpers import (
    PromoteError,
    build_bundle_dir_name,
    discover_sidecar_auto,
    load_and_validate_sidecar,
    sanitize_bundle_name,
)
from tools._sidecar_schema import SIDECAR_SUFFIX

_SCHEMA_FIXTURE = (
    Path(__file__).resolve().parents[1] / "testdata" / "extraction-sidecar-v1.yaml"
)

_VALID_SHA = "9f3a12c8e0b41a" + "0" * 50
_OTHER_SHA = "abcdef0123456789" + "0" * 48


def _source_bytes() -> bytes:
    return b"%PDF-1.4 test source for promote\n"


def _source_sha(data: bytes | None = None) -> str:
    payload = data if data is not None else _source_bytes()
    return hashlib.sha256(payload).hexdigest()


def _frontmatter(source_path: str, source_sha: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "naming_version": 1,
        "canonical": True,
        "partial": False,
        "page_spec": "all",
        "args_hash": None,
        "args_hash_prefix": None,
        "default_profile": "document-extraction-v1",
        "source_path": source_path,
        "source_sha256": source_sha,
        "source_size": len(_source_bytes()),
        "extracted_at": "2026-05-23T20:00:00Z",
        "model": "openai/gpt-5.4",
        "dpi": 200,
        "pages": "all",
        "prompt_hash": _VALID_SHA,
        "extraction_type": "ocr_transcription",
        "tool_version": "extract_document/1.0",
    }
    base.update(overrides)
    return base


def _write_sidecar(path: Path, fm: dict[str, Any], body: str = "OCR text") -> None:
    path.write_text(format_sidecar(fm, body), encoding="utf-8")


@pytest.fixture(autouse=True)
def _files_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Install schema under tmp_path and point FILES_ROOT at it."""
    schema_dir = tmp_path / "configs" / "schemas"
    schema_dir.mkdir(parents=True)
    (schema_dir / "extraction-sidecar-v1.yaml").write_text(
        _SCHEMA_FIXTURE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for mod in (
        sidecar_schema,
        promote_document_helpers,
    ):
        monkeypatch.setattr(mod, "FILES_ROOT", tmp_path)
    from tools import _extraction_profile as extraction_profile
    from tools import _file_helpers as file_helpers

    monkeypatch.setattr(file_helpers, "FILES_ROOT", tmp_path)
    monkeypatch.setattr(extraction_profile, "FILES_ROOT", tmp_path)
    sidecar_schema.load_schema.cache_clear()
    yield tmp_path
    sidecar_schema.load_schema.cache_clear()


def _stage_dropbox(
    root: Path,
    *,
    rel: str = "dropbox/legal/bill.pdf",
    data: bytes | None = None,
    with_sidecar: bool = True,
    sidecar_overrides: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Write source (+ optional canonical sidecar); return (rel, sha)."""
    payload = data if data is not None else _source_bytes()
    sha = _source_sha(payload)
    abs_path = root / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(payload)
    if with_sidecar:
        fm = _frontmatter(rel, sha, **(sidecar_overrides or {}))
        sidecar = abs_path.parent / f"{abs_path.name}{SIDECAR_SUFFIX}"
        _write_sidecar(sidecar, fm)
    return rel, sha


# ─── helper unit tests ───────────────────────────────────────────────────────


def test_sanitize_bundle_name_strips_extension() -> None:
    assert sanitize_bundle_name("bill.pdf") == "bill"


def test_build_bundle_dir_name_uses_hash_prefix() -> None:
    name = build_bundle_dir_name(
        promoted_date="2026-05-23",
        content_hash=_VALID_SHA,
        sanitized_name="bill",
    )
    assert name == f"2026-05-23_{_VALID_SHA[:12]}_bill"


def test_discover_sidecar_prefers_canonical(tmp_path: Path) -> None:
    rel, _sha = _stage_dropbox(tmp_path)
    source = tmp_path / rel
    partial_fm = _frontmatter(
        rel, _source_sha(), canonical=False, partial=True, page_spec="2-4"
    )
    partial = source.parent / "bill.pdf.pages-2-4.extracted.md"
    _write_sidecar(partial, partial_fm)
    found = discover_sidecar_auto(source)
    assert found.name == "bill.pdf.extracted.md"


def test_load_and_validate_sidecar_rejects_sha_mismatch(tmp_path: Path) -> None:
    rel, _sha = _stage_dropbox(tmp_path)
    sidecar = tmp_path / rel.replace(".pdf", ".pdf.extracted.md")
    with pytest.raises(PromoteError) as exc_info:
        load_and_validate_sidecar(
            sidecar,
            source_rel=rel,
            source_sha256=_OTHER_SHA,
        )
    assert exc_info.value.code == "source_sha_mismatch"


# ─── promotion integration (mocked cortex) ───────────────────────────────────


@pytest.fixture
def cortex_mock(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """In-memory cortex stub for entity_get / entity_create / entities."""
    state: dict[str, Any] = {
        "entities": {},
        "create_calls": 0,
    }

    def cx(
        method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        assert method == "POST" and path == "/dispatch"
        tool = body["tool"]
        args = body["arguments"]
        if tool == "entity_get":
            eid = args["entity_id"]
            if eid not in state["entities"]:
                return {
                    "error": "cortex-api error: HTTP 404 — not found",
                    "status_code": 404,
                }
            return copy.deepcopy(state["entities"][eid])
        if tool == "entities":
            ch_filter = args.get("content_hash")
            type_filter = args.get("type")
            rows = [
                {"id": eid, "content_hash": row.get("content_hash")}
                for eid, row in state["entities"].items()
                if (type_filter is None or row.get("type") == type_filter)
                and (
                    ch_filter is None
                    or row.get("content_hash") == ch_filter
                    or row.get("content_hash") == ch_filter.removeprefix("sha256:")
                )
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
        if tool == "entity_update":
            eid = args["entity_id"]
            if eid not in state["entities"]:
                return {"error": "not found", "status_code": 404}
            row = state["entities"][eid]
            row.update({k: v for k, v in args.items() if k != "entity_id"})
            state["update_calls"] = state.get("update_calls", 0) + 1
            return {"id": eid, "updated": True}
        raise AssertionError(f"unexpected tool {tool!r}")

    monkeypatch.setattr(evidence_entity_ops, "cx", cx)
    return state


def test_promote_happy_path_creates_bundle(
    tmp_path: Path,
    cortex_mock: dict[str, Any],
) -> None:
    rel, sha = _stage_dropbox(tmp_path)
    result = promote_mod._promote(
        path=rel,
        entity_id="document:bill-2026-05",
        entity_description="Property tax bill",
        entity_attributes=None,
        sidecar="auto",
    )
    assert result["entity_created"] is True
    assert result["entity_id"] == "document:bill-2026-05"
    assert result["content_hash"] == sha
    assert result["bundle_path"].startswith("evidence/")
    assert result["source_uri"].startswith("cortex://evidence/")
    assert result["sidecar_moved"]["status"] == "moved"
    assert result["sidecar_moved"]["source_sha256_verified"] is True
    assert result["canonical"] is True
    assert result["partial"] is False
    bundle_abs = tmp_path / result["bundle_path"].rstrip("/")
    assert (bundle_abs / "bill.pdf").is_file()
    assert (bundle_abs / "bill.pdf.extracted.md").is_file()
    manifest = yaml.safe_load((bundle_abs / "manifest.json").read_text())
    assert manifest["entity_id"] == "document:bill-2026-05"
    assert manifest["content_hash"] == sha
    assert not (tmp_path / rel).exists()
    assert cortex_mock["create_calls"] == 1


def test_promote_entity_conflict_preserves_staging(
    tmp_path: Path,
    cortex_mock: dict[str, Any],
) -> None:
    rel, sha = _stage_dropbox(tmp_path)
    cortex_mock["entities"]["document:bill"] = {
        "id": "document:bill",
        "type": "document",
        "content_hash": _OTHER_SHA,
    }
    with pytest.raises(PromoteError) as exc_info:
        promote_mod._promote(
            path=rel,
            entity_id="document:bill",
            entity_description="desc",
            entity_attributes=None,
            sidecar="auto",
        )
    assert exc_info.value.code == "entity_conflict"
    assert (tmp_path / rel).is_file()
    assert cortex_mock["create_calls"] == 0


def test_promote_duplicate_evidence_preserves_staging(
    tmp_path: Path,
    cortex_mock: dict[str, Any],
) -> None:
    rel, sha = _stage_dropbox(tmp_path)
    cortex_mock["entities"]["document:other"] = {
        "id": "document:other",
        "type": "document",
        "content_hash": sha,
    }
    with pytest.raises(PromoteError) as exc_info:
        promote_mod._promote(
            path=rel,
            entity_id="document:new",
            entity_description="desc",
            entity_attributes=None,
            sidecar="auto",
        )
    assert exc_info.value.code == "duplicate_evidence"
    assert (tmp_path / rel).is_file()


def test_promote_idempotent_entity_skips_create(
    tmp_path: Path,
    cortex_mock: dict[str, Any],
) -> None:
    rel, sha = _stage_dropbox(tmp_path)
    cortex_mock["entities"]["document:bill"] = {
        "id": "document:bill",
        "type": "document",
        "content_hash": sha,
    }
    result = promote_mod._promote(
        path=rel,
        entity_id="document:bill",
        entity_description="desc",
        entity_attributes=None,
        sidecar="auto",
    )
    assert result["entity_created"] is False
    assert cortex_mock["create_calls"] == 0
    assert (tmp_path / result["bundle_path"].rstrip("/") / "bill.pdf").is_file()


def test_promote_partial_sidecar_explicit_path(
    tmp_path: Path,
    cortex_mock: dict[str, Any],
) -> None:
    rel, sha = _stage_dropbox(tmp_path, with_sidecar=False)
    partial_rel = rel.replace(".pdf", ".pdf.pages-2-4.extracted.md")
    fm = _frontmatter(
        rel,
        sha,
        canonical=False,
        partial=True,
        page_spec="2-4",
        pages=[2, 3, 4],
    )
    _write_sidecar(tmp_path / partial_rel, fm)
    result = promote_mod._promote(
        path=rel,
        entity_id="document:bill-partial",
        entity_description="partial",
        entity_attributes=None,
        sidecar=partial_rel,
    )
    assert result["partial"] is True
    assert result["canonical"] is False
    bundle = tmp_path / result["bundle_path"].rstrip("/")
    assert (bundle / "bill.pdf.pages-2-4.extracted.md").is_file()


def test_promote_uses_sidecar_title_not_file_stem(
    tmp_path: Path,
    cortex_mock: dict[str, Any],
) -> None:
    rel, _sha = _stage_dropbox(tmp_path)
    sidecar = tmp_path / rel.replace(".pdf", ".pdf.extracted.md")
    fm = _frontmatter(rel, _source_sha())
    _write_sidecar(
        sidecar,
        fm,
        body="# Confirmation - Wells Fargo\n\nPayment sent.\n",
    )
    result = promote_mod._promote(
        path=rel,
        entity_id="document:confirmation-wells-fargo",
        entity_description="WF confirmation",
        entity_attributes=None,
        sidecar="auto",
    )
    created = cortex_mock["entities"]["document:confirmation-wells-fargo"]
    assert created["name"] == "Confirmation - Wells Fargo"
    assert result["entity_name"] == "Confirmation - Wells Fargo"


def test_promote_links_accounts_by_last4_no_match_still_succeeds(
    tmp_path: Path,
    cortex_mock: dict[str, Any],
) -> None:
    rel, _sha = _stage_dropbox(tmp_path)
    sidecar = tmp_path / rel.replace(".pdf", ".pdf.extracted.md")
    fm = _frontmatter(rel, _source_sha())
    _write_sidecar(
        sidecar,
        fm,
        body="# Stmt\n\nCard ending in 9999 balance $1.\n",
    )
    result = promote_mod._promote(
        path=rel,
        entity_id="document:no-account-match",
        entity_description="desc",
        entity_attributes=None,
        sidecar="auto",
    )
    assert result["entity_created"] is True
    assert result["account_link"]["accounts_linked"] == []


def test_promote_wells_fargo_falsifier_links_account_and_names_from_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falsifier fixture: sidecar ...9062 + account:wells-fargo-credit_card-9062."""
    state: dict[str, Any] = {
        "entities": {
            "account:wells-fargo-credit_card-9062": {
                "id": "account:wells-fargo-credit_card-9062",
                "type": "account",
                "attributes": {"last4": "9062"},
            }
        },
        "relationships": [],
        "assertions": [],
    }

    def cx(
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert method == "POST" and path == "/dispatch"
        tool = body["tool"]
        args = body["arguments"]
        if tool == "entity_get":
            eid = args["entity_id"]
            if eid not in state["entities"]:
                return {"error": "not found", "status_code": 404}
            return copy.deepcopy(state["entities"][eid])
        if tool == "entities":
            type_filter = args.get("type")
            rows = [
                {"id": eid, **row}
                for eid, row in state["entities"].items()
                if type_filter is None or row.get("type") == type_filter
            ]
            return {"items": rows}
        if tool == "entity_create":
            eid = args["id"]
            if eid in state["entities"]:
                return {"error": "conflict", "status_code": 409}
            state["entities"][eid] = {**args, "type": "document"}
            return {"id": eid}
        if tool == "relationship_create":
            state["relationships"].append(args)
            return {"created": True}
        if tool == "assert":
            state["assertions"].append(args)
            return {"id": len(state["assertions"])}
        raise AssertionError(tool)

    monkeypatch.setattr(evidence_entity_ops, "cx", cx)
    monkeypatch.setattr(
        "tools._promote_sidecar_linking.cx",
        cx,
    )

    rel = "dropbox/cortex_finance/2026-10-21/Confirmation - Wells Fargo.pdf"
    payload = _source_bytes()
    sha = _source_sha(payload)
    abs_path = tmp_path / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(payload)
    sidecar = abs_path.parent / f"{abs_path.name}{SIDECAR_SUFFIX}"
    fm = _frontmatter(rel, sha)
    body = (
        "# Confirmation - Wells Fargo\n\n"
        "Autograph Visa ...9062 outstanding $9,543.64\n"
        "confirmation IB03QZRW3Z amount $252.00\n"
    )
    _write_sidecar(sidecar, fm, body=body)

    result = promote_mod._promote(
        path=rel,
        entity_id="document:confirmation-wells-fargo",
        entity_description="Wells Fargo payment confirmation",
        entity_attributes=None,
        sidecar="auto",
    )
    assert result["entity_name"] == "Confirmation - Wells Fargo"
    assert result["account_link"]["accounts_linked"] == [
        "account:wells-fargo-credit_card-9062"
    ]
    rels = state["relationships"]
    assert rels
    assert rels[0]["source_id"] == "document:confirmation-wells-fargo"
    assert rels[0]["target_id"] == "account:wells-fargo-credit_card-9062"
    assert state["assertions"]
    assert state["assertions"][0]["entity_id"] == "account:wells-fargo-credit_card-9062"
    assert result["source_uri"] in state["assertions"][0]["evidence_uris"]


def test_repromote_renames_existing_document_from_generic_sidecar_heading(
    tmp_path: Path,
    cortex_mock: dict[str, Any],
) -> None:
    rel = "dropbox/cortex_finance/2026-10-21/Confirmation - Wells Fargo.pdf"
    payload = _source_bytes()
    sha = _source_sha(payload)
    abs_path = tmp_path / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(payload)
    sidecar = abs_path.parent / f"{abs_path.name}{SIDECAR_SUFFIX}"
    fm = _frontmatter(rel, sha)
    sidecar_body = (
        "# Confirmation\n\n"
        "To **WELLS FARGO AUTOGRAPH VISA CARD®** ending in 9062 "
        "(Outstanding balance = $9,543.64)\n"
    )
    _write_sidecar(sidecar, fm, body=sidecar_body)

    cortex_mock["entities"]["document:confirmation-wells-fargo"] = {
        "id": "document:confirmation-wells-fargo",
        "type": "document",
        "name": "confirmation wells fargo",
        "content_hash": sha,
    }

    result = promote_mod._promote(
        path=rel,
        entity_id="document:confirmation-wells-fargo",
        entity_description="Wells Fargo payment confirmation",
        entity_attributes=None,
        sidecar="auto",
    )

    assert cortex_mock.get("update_calls") == 1
    updated = cortex_mock["entities"]["document:confirmation-wells-fargo"]
    assert updated["name"] == "Confirmation - Wells Fargo"
    assert result["entity_name"] == "Confirmation - Wells Fargo"
    assert result["entity_created"] is False
