"""Lock the five verb-orientation blocks to config/mcp/verb-orientation.yaml."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "gen-mcp-verb-orientation"
_PY = Path.home() / ".venvs" / "universal" / "bin" / "python"


def _load():
    # Extensionless scripts make spec_from_file_location return None.
    loader = importlib.machinery.SourceFileLoader(
        "gen_mcp_verb_orientation", str(_SCRIPT)
    )
    spec = importlib.util.spec_from_loader("gen_mcp_verb_orientation", loader)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_check_exits_zero() -> None:
    proc = subprocess.run(
        [str(_PY), str(_SCRIPT), "--check"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_blocks_match_sot_and_fs_primitive() -> None:
    mod = _load()
    slate = mod._load_slate()
    mod._assert_slugs_loadable(slate)
    rendered = mod.render_all(_REPO)
    frontier = rendered[mod._TARGETS["team_dispatch"][0]]
    assert frontier.count("Depth:") == 1
    assert "agent_skill:manage" not in frontier
    assert "agent_skill:observability" not in frontier
    assert "agent_skill:tool_search" not in frontier
    events = rendered[mod._TARGETS["observability"][0]]
    head, _preview = events.split('title="Observability Preview"', 1)
    assert "«verb-orientation:observability»" in head
    assert events.count("«verb-orientation:observability»") == 1
    from claude_bundles.catalog import load_skill_catalog
    from claude_bundles.cdp_inline_read_cue import emit_workspaces_fs_read

    catalog = load_skill_catalog(_REPO / "config" / "skills.yaml", repo_root=_REPO)
    for verb, row in slate.items():
        path = mod._TARGETS[verb][0]
        text = rendered[path]
        for slug in row["cursor_only"]:
            sot, _label = catalog.resolve_sot(slug, _REPO)
            fs_line = emit_workspaces_fs_read(sot, _REPO)
            assert fs_line in text, slug
        for slug in row["shared_sync"]:
            assert f"`agent_skill:{slug}`" in text


def test_check_fails_on_drift() -> None:
    mod = _load()
    rendered = mod.render_all(_REPO)
    path = mod._TARGETS["manage"][0]
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace("agent_skill:landed-not-live", "agent_skill:missing", 1), encoding="utf-8")
    try:
        proc = subprocess.run(
            [str(_PY), str(_SCRIPT), "--check"],
            cwd=_REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode != 0
        assert "DRIFT" in proc.stdout
    finally:
        path.write_text(original, encoding="utf-8")
    assert rendered[path] == original
