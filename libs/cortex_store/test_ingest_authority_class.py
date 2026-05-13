"""Unit tests for ``IngestDocumentRequest.authority_class`` source_uri rule.

Per spec § 2.2 / § 3.2 + review finding C4: when ``authority_class`` is
provided, the chunks emitted carry pinpoint labels and the resolver
fragment lookup expects ``chunks.source_uri = cortex://<entity_id>``.
The model validator enforces this at the request boundary so a
mismatched URI cannot silently disable the verbatim gate.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cortex_store.dispatch_ops import ops_misc
from cortex_store.models import IngestDocumentRequest


def test_no_authority_class_accepts_any_source_uri() -> None:
    """Pre-spec contract preserved when authority_class is omitted."""
    req = IngestDocumentRequest(
        source_uri="https://example.com/some-doc.pdf",
        content="prose body",
    )
    assert req.authority_class is None
    assert req.source_uri == "https://example.com/some-doc.pdf"


def test_authority_class_requires_cortex_uri() -> None:
    """Non-cortex URI rejected when authority_class is set."""
    with pytest.raises(ValidationError) as exc:
        IngestDocumentRequest(
            source_uri="https://leginfo.legislature.ca.gov/rtc-63.2.html",
            content="(a) Some statute text.",
            authority_class="statute",
        )
    assert "cortex://" in str(exc.value)


def test_authority_class_with_cortex_uri_accepted() -> None:
    """Canonical cortex:// form passes the validator."""
    req = IngestDocumentRequest(
        source_uri="cortex://legal_source/rtc-63.2",
        content="(a) Some statute text.",
        authority_class="statute",
    )
    assert req.authority_class == "statute"


def test_authority_class_with_exhibit_slash_uri_accepted() -> None:
    """Slash-scoped exhibit IDs canonicalize correctly."""
    req = IngestDocumentRequest(
        source_uri="cortex://exhibit/case-slug/exhibit-slug",
        content="prose body",
        authority_class="publication",
    )
    assert req.source_uri.startswith("cortex://exhibit/")


def test_dispatch_op_forwards_authority_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP-facing cortex ingest must preserve the structured-chunker selector."""
    captured: dict[str, object] = {}

    def fake_ingest(payload: dict[str, object]) -> dict[str, object]:
        captured.update(payload)
        return {"source_uri": payload["source_uri"], "chunk_count": 0, "chunks": []}

    monkeypatch.setattr(ops_misc, "_ingest_document_impl", fake_ingest)
    monkeypatch.setattr(ops_misc, "record", lambda *args, **kwargs: None)

    result = ops_misc._op_ingest_document(
        source_uri="cortex://legal_source/rtc-63.2",
        content="(a) Some statute text.",
        authority_class="statute",
    )

    assert result["source_uri"] == "cortex://legal_source/rtc-63.2"
    assert captured["authority_class"] == "statute"
