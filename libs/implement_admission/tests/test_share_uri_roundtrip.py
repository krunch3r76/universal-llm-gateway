"""Round-trip and normalization tests for Share URI ingress/egress (Lane-A offline)."""

from __future__ import annotations

from pathlib import Path

import pytest
from implement_admission.scheme_resolve import (
    infer_sandbox_from_parsed,
    parse_schemed_path,
    resolve_fs_ingress,
    resolve_schemed_packet_file,
)
from implement_admission.share_uri_emit import to_share_uri
from implement_admission.share_uri_registry import is_cortex_entity_uri


@pytest.fixture()
def sandbox_roots(tmp_path: Path) -> tuple[Path, Path]:
    ws = tmp_path / "projects"
    cortex = tmp_path / "cortex-files"
    ws.mkdir()
    cortex.mkdir()
    ulg = ws / "universal-llm-gateway"
    ulg.mkdir()
    spec_dir = ulg / "tasks" / "specs"
    spec_dir.mkdir(parents=True)
    spec = spec_dir / "share-uri-path-unification.md"
    spec.write_text("# spec\n", encoding="utf-8")
    notes = cortex / "notes" / "system" / "specs"
    notes.mkdir(parents=True)
    cortex_spec = notes / "example.md"
    cortex_spec.write_text("# cortex spec\n", encoding="utf-8")
    return ws, cortex


def test_workspaces_share_uri_roundtrip(sandbox_roots: tuple[Path, Path]) -> None:
    ws, cortex = sandbox_roots
    uri = "workspaces://universal-llm-gateway/tasks/specs/share-uri-path-unification.md"
    resolved = resolve_schemed_packet_file(
        uri, workspaces_root_override=ws, cortex_root=cortex
    )
    assert resolved is not None
    assert to_share_uri("workspaces", resolved, workspaces_root_override=ws) == uri


def test_cortex_share_uri_roundtrip(sandbox_roots: tuple[Path, Path]) -> None:
    ws, cortex = sandbox_roots
    uri = "cortex://notes/system/specs/example.md"
    resolved = resolve_schemed_packet_file(
        uri, workspaces_root_override=ws, cortex_root=cortex
    )
    assert resolved is not None
    assert to_share_uri("cortex", resolved, cortex_root=cortex) == uri


def test_mount_path_normalizes_to_share_uri(sandbox_roots: tuple[Path, Path]) -> None:
    ws, _cortex = sandbox_roots
    abs_path = ws / "universal-llm-gateway/tasks/specs/share-uri-path-unification.md"
    ingress = resolve_fs_ingress(str(abs_path), workspaces_root_override=ws)
    assert ingress.path_input_normalized is True
    assert ingress.sandbox == "workspaces"
    assert ingress.canonical_uri.startswith("workspaces://")


def test_files_scheme_maps_to_cortex(sandbox_roots: tuple[Path, Path]) -> None:
    ws, cortex = sandbox_roots
    uri = f"files://{cortex / 'notes/system/specs/example.md'}"
    ingress = resolve_fs_ingress(
        uri, workspaces_root_override=ws, cortex_root=cortex
    )
    assert ingress.sandbox == "cortex"
    assert ingress.canonical_uri == "cortex://notes/system/specs/example.md"


def test_traversal_rejected(sandbox_roots: tuple[Path, Path]) -> None:
    ws, cortex = sandbox_roots
    with pytest.raises(ValueError, match="traversal"):
        resolve_fs_ingress(
            "workspaces://universal-llm-gateway/../../etc/passwd",
            workspaces_root_override=ws,
            cortex_root=cortex,
        )


def test_sandbox_scheme_conflict(sandbox_roots: tuple[Path, Path]) -> None:
    ws, cortex = sandbox_roots
    with pytest.raises(ValueError, match="conflicts"):
        resolve_fs_ingress(
            "cortex://notes/system/specs/example.md",
            sandbox="workspaces",
            workspaces_root_override=ws,
            cortex_root=cortex,
        )


def test_entity_uri_not_fs_resolved(sandbox_roots: tuple[Path, Path]) -> None:
    ws, cortex = sandbox_roots
    assert is_cortex_entity_uri("todo/share-uri-path-unification", cortex_root=cortex)
    assert is_cortex_entity_uri("service/foo", cortex_root=cortex)
    assert is_cortex_entity_uri("person:alice", cortex_root=cortex)
    assert not is_cortex_entity_uri("notes/system/specs/example.md", cortex_root=cortex)
    with pytest.raises(ValueError, match="entity pointer"):
        resolve_fs_ingress(
            "cortex://todo/share-uri-path-unification",
            workspaces_root_override=ws,
            cortex_root=cortex,
        )


def test_cortex_file_root_dirs_absent() -> None:
    """A7: CORTEX_FILE_ROOT_DIRS must be deleted, not merely unimported."""
    import implement_admission.scheme_resolve as scheme_resolve
    import implement_admission.share_uri_emit as share_uri_emit
    import implement_admission.share_uri_registry as share_uri_registry

    assert not hasattr(share_uri_registry, "CORTEX_FILE_ROOT_DIRS")
    assert not hasattr(scheme_resolve, "CORTEX_FILE_ROOT_DIRS")
    assert not hasattr(share_uri_emit, "CORTEX_FILE_ROOT_DIRS")
    import implement_admission as pkg

    assert "CORTEX_FILE_ROOT_DIRS" not in getattr(pkg, "__all__", ())


def test_existence_first_notes_still_file(sandbox_roots: tuple[Path, Path]) -> None:
    _ws, cortex = sandbox_roots
    parsed = parse_schemed_path("cortex://notes/foo.md")
    assert (
        infer_sandbox_from_parsed(parsed, cortex_root=cortex) == "cortex"
    )


def test_ephemeral_handoffs_share_uri_ingress(
    sandbox_roots: tuple[Path, Path],
) -> None:
    """cortex://ephemeral/… is a file-root path when the top-level exists."""
    ws, cortex = sandbox_roots
    target = cortex / "ephemeral" / "handoffs" / "probe.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("ok\n", encoding="utf-8")
    assert not is_cortex_entity_uri("ephemeral/handoffs/probe.md", cortex_root=cortex)
    ingress = resolve_fs_ingress(
        "cortex://ephemeral/handoffs/probe.md",
        workspaces_root_override=ws,
        cortex_root=cortex,
    )
    assert ingress.resolved == target.resolve()
    assert ingress.sandbox == "cortex"


def test_evidence_and_documents_ingress_when_present(
    sandbox_roots: tuple[Path, Path],
) -> None:
    ws, cortex = sandbox_roots
    for name in ("evidence", "documents"):
        (cortex / name).mkdir()
        probe = cortex / name / "probe.md"
        probe.write_text("x\n", encoding="utf-8")
        ingress = resolve_fs_ingress(
            f"cortex://{name}/probe.md",
            workspaces_root_override=ws,
            cortex_root=cortex,
        )
        assert ingress.sandbox == "cortex"
        assert ingress.resolved == probe.resolve()


def test_top_level_creation_gate_on_write(
    sandbox_roots: tuple[Path, Path],
) -> None:
    ws, cortex = sandbox_roots
    with pytest.raises(ValueError, match="create_root/mkdir"):
        resolve_fs_ingress(
            "cortex://brand-new-root/file.md",
            workspaces_root_override=ws,
            cortex_root=cortex,
            for_write=True,
        )
    # Nested under existing top-level is allowed.
    ingress = resolve_fs_ingress(
        "cortex://notes/system/new-nested.md",
        workspaces_root_override=ws,
        cortex_root=cortex,
        for_write=True,
    )
    assert ingress.sandbox == "cortex"


def test_top_level_regular_file_roundtrip(
    sandbox_roots: tuple[Path, Path],
) -> None:
    """A1: top-level regular file wins via .exists()."""
    ws, cortex = sandbox_roots
    top_file = cortex / "README.md"
    top_file.write_text("hello\n", encoding="utf-8")
    uri = to_share_uri("cortex", top_file, cortex_root=cortex)
    ingress = resolve_fs_ingress(
        uri, workspaces_root_override=ws, cortex_root=cortex
    )
    assert ingress.resolved == top_file.resolve()
    assert ingress.sandbox == "cortex"


def test_parametrized_synthetic_top_levels_roundtrip(tmp_path: Path) -> None:
    """A4: ≥8 synthetic top-level entries including hyphenated/dot/unicode/file."""
    cortex = tmp_path / "cortex"
    cortex.mkdir()
    entries = [
        ("notes", True),
        ("evidence", True),
        ("documents", True),
        ("finance", True),
        ("vehicle", True),
        ("my-hyphen", True),
        (".dot-prefixed", True),
        ("unicodé-root", True),
        ("TOPFILE.md", False),
    ]
    paths: list[Path] = []
    for name, is_dir in entries:
        if is_dir:
            d = cortex / name
            d.mkdir()
            p = d / "child.md"
            p.write_text("ok\n", encoding="utf-8")
        else:
            p = cortex / name
            p.write_text("file\n", encoding="utf-8")
        paths.append(p)

    for p in paths:
        uri = to_share_uri("cortex", p, cortex_root=cortex)
        ingress = resolve_fs_ingress(uri, cortex_root=cortex)
        assert ingress.resolved == p.resolve()
        assert ingress.sandbox == "cortex"


def test_cortex_root_override_isolates_from_live_mount(
    sandbox_roots: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A3: live mount never consulted when cortex_root override supplied."""
    ws, cortex = sandbox_roots

    def _boom() -> Path:
        raise RuntimeError("live mount consulted")

    monkeypatch.setattr(
        "implement_admission.scheme_resolve.cortex_files_root", _boom
    )
    ingress = resolve_fs_ingress(
        "cortex://notes/system/specs/example.md",
        workspaces_root_override=ws,
        cortex_root=cortex,
    )
    assert ingress.sandbox == "cortex"
    assert ingress.resolved is not None


def test_finance_vehicle_slash_vs_colon(
    sandbox_roots: tuple[Path, Path],
) -> None:
    ws, cortex = sandbox_roots
    for name in ("finance", "vehicle"):
        (cortex / name).mkdir()
        probe = cortex / name / "doc.md"
        probe.write_text("x\n", encoding="utf-8")
        slash = resolve_fs_ingress(
            f"cortex://{name}/doc.md",
            workspaces_root_override=ws,
            cortex_root=cortex,
        )
        assert slash.sandbox == "cortex"
        with pytest.raises(ValueError, match="entity pointer"):
            resolve_fs_ingress(
                f"cortex://{name}:slug",
                workspaces_root_override=ws,
                cortex_root=cortex,
            )


def test_egress_colon_segment_refused(
    sandbox_roots: tuple[Path, Path],
) -> None:
    _ws, cortex = sandbox_roots
    with pytest.raises(ValueError, match="leading segment"):
        to_share_uri("cortex", "todo:slug/x.md", cortex_root=cortex)


def test_scheme_resolve_has_no_egress_helpers() -> None:
    """Fork F guard: scheme_resolve.py is ingress-only."""
    text = Path(__file__).resolve().parents[1].joinpath("scheme_resolve.py").read_text(
        encoding="utf-8"
    )
    forbidden = ("def to_share_uri", "def dual_carry", "def sandbox_rel")
    for name in forbidden:
        assert name not in text


def test_projection_leaves_storage_unmigrated() -> None:
    stored = "/mnt/torus/mcp-data/files/notes/system/specs/legacy.md"
    from implement_admission.evidence_uri_project import (
        project_evidence_uri_for_display,
    )

    projected = project_evidence_uri_for_display(stored)
    assert projected == "cortex://notes/system/specs/legacy.md"
    assert stored.startswith("/")  # byte-unchanged source value


def test_files_scheme_egress_retired_from_projection() -> None:
    from implement_admission.evidence_uri_project import (
        project_evidence_uri_for_display,
    )

    projected = project_evidence_uri_for_display("files://notes/foo.md")
    assert projected.startswith("cortex://")
    assert not projected.startswith("files://")
