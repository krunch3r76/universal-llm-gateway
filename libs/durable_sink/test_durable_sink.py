"""Tests for DurableSink factory and path safety."""

from __future__ import annotations

from pathlib import Path

import pytest
from cortex_store.dispatch_ops import _recon_sidecar as recon_mod

from durable_sink import (
    CortexSink,
    FilesystemSink,
    NullSink,
    resolve_durable_sink,
    write_session_rag_query_sidecar,
)


@pytest.mark.offline
def test_resolve_explicit_null() -> None:
    resolved = resolve_durable_sink(
        backend_override="null",
        probe_fn=lambda: "ok",
    )
    assert resolved.metadata.selected_backend == "null"
    assert resolved.metadata.selection_reason == "explicit_config"
    assert resolved.metadata.cortex_probe_status == "not_probed"
    assert resolved.metadata.fallback_used is False
    assert isinstance(resolved.sink, NullSink)


@pytest.mark.offline
def test_resolve_explicit_filesystem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DURABLE_SINK_FS_ROOT", str(tmp_path))
    resolved = resolve_durable_sink(
        backend_override="filesystem",
        probe_fn=lambda: "unreachable",
    )
    assert resolved.metadata.selected_backend == "filesystem"
    assert resolved.metadata.fallback_used is False
    assert isinstance(resolved.sink, FilesystemSink)


def test_resolve_explicit_filesystem_missing_root_raises() -> None:
    with pytest.raises(RuntimeError, match="filesystem root"):
        resolve_durable_sink(
            backend_override="filesystem",
            probe_fn=lambda: "unreachable",
        )


@pytest.mark.offline
def test_resolve_auto_cortex_up() -> None:
    resolved = resolve_durable_sink(
        backend_override="auto",
        probe_fn=lambda: "ok",
    )
    assert resolved.metadata.selected_backend == "cortex"
    assert resolved.metadata.selection_reason == "auto_probe_ok"
    assert resolved.metadata.cortex_probe_status == "ok"
    assert isinstance(resolved.sink, CortexSink)


@pytest.mark.offline
def test_resolve_auto_cortex_down_null_fallback() -> None:
    resolved = resolve_durable_sink(
        backend_override="auto",
        probe_fn=lambda: "unreachable",
    )
    assert resolved.metadata.selected_backend == "null"
    assert resolved.metadata.fallback_used is True
    assert resolved.metadata.selection_reason == "auto_probe_failed_fallback"
    assert isinstance(resolved.sink, NullSink)


@pytest.mark.offline
def test_resolve_auto_cortex_down_filesystem_fallback(tmp_path: Path) -> None:
    monkey = pytest.MonkeyPatch()
    monkey.setenv("DURABLE_SINK_FS_ROOT", str(tmp_path))
    try:
        resolved = resolve_durable_sink(
            backend_override="auto",
            probe_fn=lambda: "unreachable",
        )
    finally:
        monkey.undo()
    assert resolved.metadata.selected_backend == "filesystem"
    assert resolved.metadata.fallback_used is True
    assert isinstance(resolved.sink, FilesystemSink)


@pytest.mark.offline
def test_resolve_explicit_cortex_unreachable_raises() -> None:
    with pytest.raises(RuntimeError, match="refusing silent NullSink"):
        resolve_durable_sink(
            backend_override="cortex",
            probe_fn=lambda: "unreachable",
        )


@pytest.mark.offline
def test_null_sink_returns_none() -> None:
    resolved = resolve_durable_sink(backend_override="null", probe_fn=lambda: "ok")
    assert (
        resolved.sink.write_recon_sidecar("lbl", "theme", "body") is None
    )


@pytest.mark.offline
def test_f4_traversal_inputs_stay_confined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(recon_mod, "_FILES_ROOT", tmp_path)
    resolved = recon_mod.resolve_recon_target("../../threads", "../x")
    assert resolved is not None
    root = recon_mod._recon_root().resolve()
    _label_slug, _theme_slug, target = resolved
    assert target.resolve().relative_to(root)
    result_path = recon_mod.write_recon_sidecar_file(
        "../../threads",
        "../x",
        recon_mod.render_recon_sidecar_markdown(
            label="../../threads",
            theme="../x",
            body="body",
            scopes=[],
            queries=[],
            sink_backend="cortex",
            sha256="abc",
        ),
    )
    assert str(tmp_path) in result_path


@pytest.mark.offline
def test_session_close_consumer_uses_durable_sink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DURABLE_SINK_FS_ROOT", str(tmp_path))
    payload = write_session_rag_query_sidecar(
        "sess-1",
        "sess-1",
        "rag-queries",
        "## appendix\n",
        scopes=["research"],
        queries=["embedding strategies"],
        backend_override="filesystem",
    )
    assert payload["selected_backend"] == "filesystem"
    assert payload["uri"].startswith("file://")
    assert "sha256" in payload


@pytest.mark.offline
def test_cortex_sink_dispatches_recon_sidecar_write() -> None:
    calls: list[tuple[str, dict]] = []

    def dispatch(tool: str, args: dict) -> dict:
        calls.append((tool, args))
        return {
            "uri": "cortex://notes/system/recon/lbl/theme.md",
            "sha256": "abc",
        }

    sink = CortexSink(dispatch)
    result = sink.write_recon_sidecar(
        "lbl",
        "theme",
        "body",
        scopes=["s"],
        queries=["q"],
    )
    assert calls[0][0] == "recon_sidecar_write"
    assert result is not None
    assert result.uri.startswith("cortex://")


@pytest.mark.offline
def test_discards_advisory_skip_without_discards_section() -> None:
    body = "### Query: foo [SKIP]\n\n_No results._\n"
    assert recon_mod.discards_advisory(body) is not None


@pytest.mark.offline
def test_discards_advisory_none_when_discards_present() -> None:
    body = "### Query: foo [SKIP]\n\n## Discards\n\n- foo — reason\n"
    assert recon_mod.discards_advisory(body) is None


@pytest.mark.offline
def test_discards_advisory_none_without_skip_tags() -> None:
    body = "## Results\n\n### Query: foo [RELEVANT]\n"
    assert recon_mod.discards_advisory(body) is None


@pytest.mark.offline
def test_recon_sidecar_write_warn_not_reject_on_missing_discards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store.dispatch_ops.ops_misc import _op_recon_sidecar_write

    monkeypatch.setattr(recon_mod, "_FILES_ROOT", tmp_path)
    body = "### Query: foo [SKIP]\n\n_No results._\n"
    result = _op_recon_sidecar_write(
        label="lbl",
        theme="theme",
        body=body,
        scopes=["s"],
        queries=["foo"],
    )
    assert "error" not in result
    assert "discards_advisory" in result
    written = (
        tmp_path / "notes/system/recon/lbl/theme.md"
    ).read_text(encoding="utf-8")
    assert "## Discards" not in written
    assert body in written
