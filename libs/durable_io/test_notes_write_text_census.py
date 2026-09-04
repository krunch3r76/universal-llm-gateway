"""AC4 class gate: leftover Path.write_text on notes-writer trees is a defect.

S1's named-module grep was a list. This test is the class: production
``.write_text(`` in notes-capable trees fails unless the file is on the
out-of-class allowlist (reason: does not write the cortex notes tree).
Tests are skipped. The durable leaf itself must not call Path.write_text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

# Packages/paths that may write the cortex notes (or cortex-files) tree.
_TREES: tuple[str, ...] = (
    "libs/cortex_store/",
    "libs/agent_bus_store/",
    "libs/durable_io/",
    "libs/durable_sink/",
    "libs/implement_admission/",
    "services/mcp-server/tools/",
    "services/universal-stargate/systems/frontier_consult/",
    "services/git_integration_worker/trigger_service/",
    "scripts/model_manager/ui/controller/charter_runner/",
)

_EXTRA_FILES: tuple[str, ...] = (
    "libs/implement_admission/consult_provenance_record.py",
    "libs/claude_bundles/cdp_model_endpoint_staging.py",
    "libs/claude_bundles/project_ask.py",
    "scripts/cortex/what_is_running.py",
    "scripts/cortex/digest_revision_opus_approve.py",
    "scripts/cortex/cdp_ask_falsifiers.py",
)

# File → why it does not write the cortex notes tree.
OUT_OF_CLASS: dict[str, str] = {
    "libs/cortex_store/dispatch_ops/_doc_gen.py": (
        "writes repo _TOOLS_PY, not the notes tree"
    ),
    "libs/cortex_store/openapi_mcp/codegen.py": (
        "writes generated module under the package, not notes"
    ),
    "libs/cortex_store/schema_snapshot.py": (
        "writes fixture JSON under the package, not notes"
    ),
    "libs/agent_bus_store/openapi_mcp/codegen.py": (
        "writes generated module under the package, not notes"
    ),
    "services/mcp-server/tools/context/journal_entries.py": (
        "writes tasks/ journal, not cortex notes"
    ),
    "services/mcp-server/tools/context/context_file_mutations.py": (
        "writes tasks/ workspace files, not cortex notes"
    ),
    "services/universal-stargate/systems/frontier_consult/route.py": (
        "mutates the handoff packet_file argument, not the notes root"
    ),
    "scripts/model_manager/ui/controller/charter_runner/dispatch_client.py": (
        "writes tmp/charter-runner packets, not notes"
    ),
    "scripts/model_manager/ui/controller/charter_runner/kernel/hold.py": (
        "writes charter-runner hold state, not notes"
    ),
    "scripts/model_manager/ui/controller/charter_runner/admission/caps.py": (
        "writes charter-runner cap/intent files, not notes"
    ),
    "scripts/model_manager/ui/controller/charter_runner/root_health.py": (
        "writes charter-runner health JSON, not notes"
    ),
    "scripts/model_manager/ui/controller/charter_runner/storm_fuse.py": (
        "writes charter-runner fuse state, not notes"
    ),
    "scripts/model_manager/ui/controller/charter_runner/r_corpus_sha.py": (
        "writes charter-runner corpus counter, not notes"
    ),
    "scripts/model_manager/ui/controller/charter_runner/window_log.py": (
        "writes charter-runner harvest/token markers, not notes"
    ),
    "scripts/model_manager/ui/controller/charter_runner/residue_store.py": (
        "writes charter-runner residue JSON, not notes"
    ),
    "scripts/model_manager/ui/controller/charter_runner/verification_manifest.py": (
        "writes charter-runner verification manifests, not notes"
    ),
    "scripts/model_manager/ui/controller/charter_runner/test_support/reference_age_watch.py": (
        "writes charter-runner test-support watch JSON, not notes"
    ),
    "libs/implement_admission/materialize.py": (
        "writes dispatch packets to caller out_dir, not the notes tree"
    ),
    "libs/implement_admission/conductor_materialize.py": (
        "writes conductor packet to caller out_dir, not the notes tree"
    ),
}


def _is_test(path: Path) -> bool:
    name = path.name
    if name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py":
        return True
    return any(part in {"test", "tests"} for part in path.parts)


def _iter_scanned_files() -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for rel in _TREES:
        base = _REPO / rel
        if base.is_file():
            candidates = [base]
        elif base.is_dir():
            candidates = list(base.rglob("*.py"))
        else:
            continue
        for path in candidates:
            if "__pycache__" in path.parts or _is_test(path):
                continue
            if path not in seen:
                seen.add(path)
                files.append(path)
    for rel in _EXTRA_FILES:
        path = _REPO / rel
        if path.is_file() and path not in seen and not _is_test(path):
            seen.add(path)
            files.append(path)
    return files


def bare_write_text_hits() -> list[tuple[str, int, str]]:
    """Return (repo-rel, line, snippet) for production ``.write_text(`` calls."""
    hits: list[tuple[str, int, str]] = []
    for path in _iter_scanned_files():
        rel = path.relative_to(_REPO).as_posix()
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if ".write_text(" not in stripped:
                continue
            # Adapter delegates to CloseoutRuntime.write_text (the funnelled default).
            if stripped.startswith("rt.write_text("):
                continue
            hits.append((rel, i, stripped))
    return hits


@pytest.mark.offline
def test_notes_writer_trees_have_no_unclassified_write_text() -> None:
    """Default-deny: new notes-tree Path.write_text fails CI (AC4)."""
    unknown: list[str] = []
    for rel, line_no, snippet in bare_write_text_hits():
        if rel in OUT_OF_CLASS:
            continue
        unknown.append(f"{rel}:{line_no}:{snippet}")
    assert unknown == [], (
        "bare Path.write_text in a notes-writer tree; funnel through "
        "durable_io.atomic or add to OUT_OF_CLASS with a not-notes reason:\n"
        + "\n".join(unknown)
    )


@pytest.mark.offline
def test_out_of_class_allowlist_paths_exist() -> None:
    missing = [rel for rel in OUT_OF_CLASS if not (_REPO / rel).is_file()]
    assert missing == [], missing


@pytest.mark.offline
def test_durable_leaf_does_not_call_path_write_text() -> None:
    text = (_REPO / "libs/durable_io/atomic.py").read_text(encoding="utf-8")
    assert ".write_text(" not in text
