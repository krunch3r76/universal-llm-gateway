"""Integration tests for rag(op=recon) durable sink semantics."""

from __future__ import annotations

from typing import Any

import pytest
from cortex_store.dispatch_ops._recon_sidecar import (
    recon_sidecar_frontmatter_line_count,
    render_recon_sidecar_markdown,
)
from durable_sink import NullSink, ResolvedDurableSink, SinkSelectionMetadata
from tools._rag_recon import _build_theme_markdown, execute_rag_recon

_MULTI_SOURCE_CONTEXT = """\
[Source: notes/system/specs/recon.md | Last changed: 2026-01-01T00:00:00Z]
[Body evidence]
First evidence line about recon manifests and durable sinks.

---

[Source: workspaces://repo/docs/guide.md | Last changed: 2026-01-02T00:00:00Z]
[Section heading — retrieval hint only, not evidence]
Guide section

[Body evidence]
Second evidence discusses navigation tax for large sidecars."""


def _fake_search(_query: str, _scopes: list[str], *, top_k: int) -> dict[str, Any]:
    return {
        "context": "chunk text",
        "content_length": 10,
        "chunks_found": 2,
        "retrieval": {"chunks_found": 2},
    }


def _multi_source_search(query: str, _scopes: list[str], *, top_k: int) -> dict[str, Any]:
    if query == "miss":
        return {"context": "", "content_length": 0, "chunks_found": 0}
    if query == "broken":
        return {"error": "pipeline timeout"}
    return {
        "context": _MULTI_SOURCE_CONTEXT,
        "content_length": len(_MULTI_SOURCE_CONTEXT),
        "chunks_found": 4,
        "retrieval": {"chunks_found": 4},
    }


def _null_resolved() -> ResolvedDurableSink:
    return ResolvedDurableSink(
        sink=NullSink(),
        metadata=SinkSelectionMetadata(
            selected_backend="null",
            selection_reason="auto_probe_failed_fallback",
            cortex_probe_status="unreachable",
            fallback_used=True,
        ),
    )


@pytest.mark.offline
def test_f5_auto_unreachable_null_warning_no_fake_cortex_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tools._rag_recon._run_theme_search", _fake_search)
    monkeypatch.setattr("tools._rag_recon.resolve_durable_sink", lambda **_kw: _null_resolved())

    result = execute_rag_recon(
        "test-label",
        [
            {
                "name": "theme-a",
                "scopes": ["research"],
                "queries": ["query one"],
            }
        ],
        durable_sink="auto",
    )
    assert result["status"] == "ok"
    assert result["fallback_used"] is True
    assert result["selected_backend"] == "null"
    assert "warning" in result
    assert "evidence_uris" not in result
    assert result["themes"][0].get("uri") is None
    assert "source_manifest" in result["themes"][0]


@pytest.mark.offline
def test_f5_explicit_cortex_unreachable_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tools._rag_recon._run_theme_search", _fake_search)

    def _raise(**_kwargs: object) -> ResolvedDurableSink:
        raise RuntimeError(
            "durable_sink=cortex but cortex is unreachable; refusing silent NullSink"
        )

    monkeypatch.setattr("tools._rag_recon.resolve_durable_sink", _raise)
    result = execute_rag_recon(
        "test-label",
        [{"name": "t", "scopes": ["research"], "queries": ["q"]}],
        durable_sink="cortex",
    )
    assert "error" in result
    assert "refusing silent NullSink" in result["error"]


@pytest.mark.offline
def test_recon_end_to_end_cortex_uri(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store.dispatch_ops import _recon_sidecar as recon_mod

    monkeypatch.setattr(recon_mod, "_FILES_ROOT", tmp_path)
    monkeypatch.setattr("tools._rag_recon._run_theme_search", _fake_search)

    def dispatch(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        from cortex_store.dispatch_ops.ops_misc import _op_recon_sidecar_write

        assert tool == "recon_sidecar_write"
        return _op_recon_sidecar_write(**args)

    result = execute_rag_recon(
        "e2e-label",
        [{"name": "theme-one", "scopes": ["research"], "queries": ["q1"]}],
        durable_sink="cortex",
        dispatch_fn=dispatch,
        probe_fn=lambda: "ok",
    )
    assert result["status"] == "ok"
    assert result["selected_backend"] == "cortex"
    theme = result["themes"][0]
    assert theme["uri"] == "cortex://notes/system/recon/e2e-label/theme-one.md"
    assert result["evidence_uris"] == [theme["uri"]]
    written = (
        tmp_path / "notes/system/recon/e2e-label/theme-one.md"
    ).read_text(encoding="utf-8")
    assert "label: e2e-label" in written
    assert "sha256:" in written
    assert "## Source manifest" in written
    assert written.index("## Source manifest") < written.index("## Results")
    assert "## Discards" in written
    assert "source_manifest" in theme


@pytest.mark.offline
def test_build_theme_markdown_discards_none_when_no_skips() -> None:
    body, _manifest = _build_theme_markdown(
        theme="t",
        scopes=["research"],
        queries=["q1"],
        query_results=[{"context": "x" * 500, "content_length": 500, "chunks_found": 2}],
    )
    assert "## Source manifest" in body
    assert body.index("## Source manifest") < body.index("## Results")
    assert "## Discards" in body
    assert "_None._" in body
    assert "below MARGINAL threshold" not in body


@pytest.mark.offline
def test_build_theme_markdown_discards_one_line_per_skip() -> None:
    body, _manifest = _build_theme_markdown(
        theme="t",
        scopes=["research"],
        queries=["hit", "miss"],
        query_results=[
            {"context": "x" * 500, "content_length": 500, "chunks_found": 2},
            {"context": "", "content_length": 0, "chunks_found": 0},
        ],
    )
    assert "## Discards" in body
    assert "`miss` — no relevant hits (below MARGINAL threshold)" in body
    assert "`hit`" not in body.split("## Discards", maxsplit=1)[1]


@pytest.mark.offline
def test_source_manifest_multi_query_sources_and_order() -> None:
    fm_lines = recon_sidecar_frontmatter_line_count()
    body, manifest = _build_theme_markdown(
        theme="nav-theme",
        scopes=["research"],
        queries=["hit", "miss", "broken"],
        query_results=[
            {
                "context": _MULTI_SOURCE_CONTEXT,
                "content_length": len(_MULTI_SOURCE_CONTEXT),
                "chunks_found": 4,
            },
            {"context": "", "content_length": 0, "chunks_found": 0},
            {"error": "pipeline timeout"},
        ],
        frontmatter_line_count=fm_lines,
    )
    assert body.index("## Source manifest") < body.index("## Results")
    assert len(manifest) == 3

    hit_entry = manifest[0]
    assert hit_entry["query"] == "hit"
    assert hit_entry["tag"] == "RELEVANT"
    assert len(hit_entry["sources"]) == 2
    assert hit_entry["sources"][0]["label"] == "notes/system/specs/recon.md"
    assert hit_entry["sources"][1]["label"] == "workspaces://repo/docs/guide.md"
    for source in hit_entry["sources"]:
        assert len(source["lead"]) <= 120
        assert "line=" in body or f"line={source['line']}" in body

    skip_entry = manifest[1]
    assert skip_entry["query"] == "miss"
    assert skip_entry["tag"] == "SKIP"
    assert skip_entry["sources"] == []
    assert skip_entry["note"] == "no relevant hits (below MARGINAL threshold)"
    assert "_(none — no relevant hits" in body

    error_entry = manifest[2]
    assert error_entry["query"] == "broken"
    assert error_entry["tag"] is None
    assert error_entry["sources"] == []
    assert error_entry["error"] == "pipeline timeout"
    assert "_(none — search failed: pipeline timeout)_" in body


@pytest.mark.offline
def test_source_manifest_line_anchors_resolve_in_persisted_file() -> None:
    fm_lines = recon_sidecar_frontmatter_line_count()
    body, manifest = _build_theme_markdown(
        theme="anchor-theme",
        scopes=["research"],
        queries=["hit"],
        query_results=[
            {
                "context": _MULTI_SOURCE_CONTEXT,
                "content_length": len(_MULTI_SOURCE_CONTEXT),
                "chunks_found": 4,
            }
        ],
        frontmatter_line_count=fm_lines,
    )
    file_content = render_recon_sidecar_markdown(
        label="anchor-label",
        theme="anchor-theme",
        body=body,
        scopes=["research"],
        queries=["hit"],
        sink_backend="cortex",
        sha256="abc",
    )
    file_lines = file_content.splitlines()
    for source in manifest[0]["sources"]:
        anchored = file_lines[source["line"] - 1]
        assert anchored.startswith("[Source:")


@pytest.mark.offline
def test_execute_rag_recon_envelope_source_manifest_isomorphic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tools._rag_recon._run_theme_search", _multi_source_search)
    monkeypatch.setattr("tools._rag_recon.resolve_durable_sink", lambda **_kw: _null_resolved())

    result = execute_rag_recon(
        "manifest-label",
        [
            {
                "name": "theme-a",
                "scopes": ["research"],
                "queries": ["hit", "miss", "broken"],
            }
        ],
        durable_sink="auto",
    )
    theme = result["themes"][0]
    manifest = theme["source_manifest"]
    assert len(manifest) == 3
    assert manifest[0]["sources"]
    assert manifest[1]["note"]
    assert manifest[1]["sources"] == []
    assert manifest[2]["error"] == "pipeline timeout"
    assert manifest[2]["sources"] == []

    body, _ = _build_theme_markdown(
        theme="theme-a",
        scopes=["research"],
        queries=["hit", "miss", "broken"],
        query_results=[
            {
                "context": _MULTI_SOURCE_CONTEXT,
                "content_length": len(_MULTI_SOURCE_CONTEXT),
                "chunks_found": 4,
            },
            {"context": "", "content_length": 0, "chunks_found": 0},
            {"error": "pipeline timeout"},
        ],
        frontmatter_line_count=recon_sidecar_frontmatter_line_count(),
    )
    for entry in manifest:
        assert entry["md_path"] in body
        for source in entry.get("sources", []):
            assert source["label"] in body
            assert f"line={source['line']}" in body
