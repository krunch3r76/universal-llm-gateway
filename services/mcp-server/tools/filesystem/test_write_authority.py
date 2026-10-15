"""Two-class write authority + episode collision replay falsifier."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.filesystem import _files_dispatcher as files_dispatcher
from tools.filesystem import _ops_text as ops_text
from tools.filesystem import _ops_write as ops_write
from tools.filesystem import _paths as paths
from tools.filesystem import _write_authority as authority


@pytest.fixture
def sandbox_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(paths, "SANDBOX_ROOT", root)
    return root


def test_consult_unguarded_overwrite_refuses_with_digest(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/6655-demo-architecture-bind.md"
    first = ops_text.write_file_impl(rel, "opus-bind", if_absent=True)
    assert first["status"] == "written"
    rejected = ops_text.write_file_impl(rel, "fable-bind")
    assert rejected["reason"] == "consult_class.unguarded_overwrite"
    assert rejected["existing_sha256"].startswith("sha256:")
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "opus-bind"


def test_consult_first_write_echoes_advisory(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """a:31802 — the refusal must be knowable from the write that creates the
    path, not only from the doomed follow-up append/replace/md_* call."""
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/6655-demo-architecture-bind.md"
    written = ops_text.write_file_impl(rel, "opus-bind", if_absent=True)
    assert written["status"] == "written"
    assert written["artifact_class"] == "consult"
    assert "Write-once" in written["consult_notice"]


def test_runbook_consult_substring_stays_editable(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """notes/runbooks/ is an invoked command, even when the slug contains -consult-."""
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/runbooks/bus-consult-watcher.md"
    assert authority.classify_artifact_path(rel) == "unclassified"
    assert (
        authority.classify_artifact_path("notes/system/threads/8801-consult-reply.md")
        == "consult"
    )
    first = ops_text.write_file_impl(rel, "staged\n")
    assert first["status"] == "written"
    assert first.get("artifact_class") != "consult"
    second = ops_text.write_file_impl(rel, "landed\n")
    assert second["status"] == "written"
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "landed\n"


def test_shared_write_has_no_consult_advisory(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/specs/demo-roadmap-2.md"
    written = ops_text.write_file_impl(rel, "row-1\n", artifact_class="shared")
    assert written["status"] == "written"
    assert "artifact_class" not in written
    assert "consult_notice" not in written


def test_consult_if_absent_collision_installs_pointer(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/6655-demo-architecture-bind.md"
    ops_text.write_file_impl(rel, "peer-a", if_absent=True, author="opus")
    rejected = ops_text.write_file_impl(rel, "peer-b", if_absent=True, author="fable")
    assert rejected["reason"] == "file_exists"
    assert rejected.get("pointer_installed") is True
    body = (sandbox_root / rel).read_text(encoding="utf-8")
    assert "FORK POINTER" in body
    assert "peer-a" not in body
    assert rejected["existing_sha256"].startswith("sha256:")
    assert rejected["attempted_sha256"].startswith("sha256:")


def test_shared_write_requires_expected_sha256(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/specs/demo-roadmap.md"
    ops_text.write_file_impl(rel, "row-1\n", artifact_class="shared")
    rejected = ops_text.write_file_impl(rel, "row-1\nrow-2\n", artifact_class="shared")
    assert rejected["reason"] == "expected_sha256.required"
    current = paths.sha256_of_file(sandbox_root / rel)
    stale = ops_text.write_file_impl(
        rel,
        "row-1\nrow-2\n",
        expected_sha256="0" * 64,
        artifact_class="shared",
    )
    assert stale["reason"] == "file_sha256.mismatch"
    ok = ops_text.write_file_impl(
        rel,
        "row-1\nrow-2\n",
        expected_sha256=current,
        artifact_class="shared",
    )
    assert ok["status"] == "written"


def test_standing_handoff_classified_shared_requires_expected_sha256(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/77-standing-handoff.md"
    ops_text.write_file_impl(rel, "leg-0\n")
    rejected = ops_text.write_file_impl(rel, "leg-0\nleg-1\n")
    assert rejected["reason"] == "expected_sha256.required"


def test_house_rules_classified_shared_requires_expected_sha256(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    ops_text.write_file_impl(rel, "rule-0\n")
    rejected = ops_text.write_file_impl(rel, "rule-0\nrule-1\n")
    assert rejected["reason"] == "expected_sha256.required"


def test_threads_other_file_stays_unclassified_no_cas(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/77-other.md"
    ops_text.write_file_impl(rel, "first\n")
    second = ops_text.write_file_impl(rel, "first\nsecond\n")
    assert second.get("reason") != "expected_sha256.required"
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "first\nsecond\n"


def test_shared_append_stale_base_refuses(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/roadmaps/feature-alignment.md"
    ops_text.write_file_impl(rel, "head\n", artifact_class="shared")
    missing = ops_text.edit_file_impl(
        rel, "append", "rank-act\n", artifact_class="shared"
    )
    assert missing["reason"] == "expected_sha256.required"
    stale = ops_text.edit_file_impl(
        rel,
        "append",
        "rank-act\n",
        expected_sha256="0" * 64,
        artifact_class="shared",
    )
    assert stale["reason"] == "file_sha256.mismatch"
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "head\n"


def test_dispatch_replace_shared_expected_sha256_succeeds_and_refuses(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatcher must forward expected_sha256 on replace (row-27 plumbing)."""
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(ops_text, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/demo-capability-roadmap.md"
    ops_text.write_file_impl(rel, "alpha\nbeta\n", artifact_class="shared")
    current = paths.sha256_of_file(sandbox_root / rel)

    missing = files_dispatcher.dispatch_files_op(
        op="replace",
        path=rel,
        target="beta",
        content="gamma",
    )
    assert missing["reason"] == "expected_sha256.required"
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "alpha\nbeta\n"

    wrong = files_dispatcher.dispatch_files_op(
        op="replace",
        path=rel,
        target="beta",
        content="gamma",
        expected_sha256="0" * 64,
    )
    assert wrong["reason"] == "file_sha256.mismatch"
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "alpha\nbeta\n"

    ok = files_dispatcher.dispatch_files_op(
        op="replace",
        path=rel,
        target="beta",
        content="gamma",
        expected_sha256=current,
    )
    assert ok.get("reason") is None
    assert "error" not in ok
    assert (sandbox_root / rel).read_text(encoding="utf-8") == "alpha\ngamma\n"


def test_mint_full_execution_id_not_exec8() -> None:
    path_a = authority.mint_consult_artifact_rel_path(
        thread="6655",
        slug="soft-defer",
        seat="cdp-opus",
        execution_id="65b24006" + ("a" * 24),
        kind="architecture",
    )
    path_b = authority.mint_consult_artifact_rel_path(
        thread="6655",
        slug="soft-defer",
        seat="cdp-fable",
        execution_id="65b24006" + ("b" * 24),
        kind="architecture",
    )
    assert path_a != path_b
    assert "65b24006aaaaaaaaaaaaaaaaaaaaaaaa" in path_a
    assert "65b24006bbbbbbbbbbbbbbbbbbbbbbbb" in path_b


def test_episode_collision_replay_both_peers_survive(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay falsifier: same-fork dual write lands two digests at distinct URIs."""
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    opus_body = "# opus peer body for soft-defer bind\n"
    fable_body = "# fable peer body for soft-defer bind\n"
    opus_path = authority.mint_consult_artifact_rel_path(
        thread="6655",
        slug="mcp-soft-defer-remedy",
        seat="cdp-opus",
        execution_id="65b24006ff7c62cc25f4175d8c19c649",
        kind="architecture-bind",
    )
    fable_path = authority.mint_consult_artifact_rel_path(
        thread="6655",
        slug="mcp-soft-defer-remedy",
        seat="cdp-fable",
        execution_id="268f8754c441bdda6f3f8bec4db7e740",
        kind="architecture-bind",
    )
    assert opus_path != fable_path
    w1 = ops_text.write_file_impl(opus_path, opus_body, if_absent=True)
    w2 = ops_text.write_file_impl(fable_path, fable_body, if_absent=True)
    assert w1["status"] == "written"
    assert w2["status"] == "written"
    assert (sandbox_root / opus_path).read_text(encoding="utf-8") == opus_body
    assert (sandbox_root / fable_path).read_text(encoding="utf-8") == fable_body
    # Slug-only collision address refuses and keeps a pointer when contested.
    collision = "notes/system/threads/6655-mcp-soft-defer-remedy-architecture-bind.md"
    ops_text.write_file_impl(collision, opus_body, if_absent=True)
    clash = ops_text.write_file_impl(collision, fable_body, if_absent=True)
    assert clash["reason"] == "file_exists"
    assert clash.get("pointer_installed") is True
    # Distinct peer addresses still hold their bodies.
    assert (sandbox_root / opus_path).read_text(encoding="utf-8") == opus_body
    assert (sandbox_root / fable_path).read_text(encoding="utf-8") == fable_body


def _house_rules_body_exactly(byte_count: int) -> str:
    intro = "## HOUSE RULES\n"
    filler = "x" * (byte_count - len(intro.encode("utf-8")))
    return intro + filler


def _assert_rotation_refusal(rejected: dict) -> None:
    assert rejected.get("reason") == "house_rules.over_budget"
    assert "rotation" in rejected["error"]
    assert "12286-rulings-archive.md" in rejected["error"]


def test_house_rules_limit_equals_rules_budget_b() -> None:
    from claude_bundles.operator_proxy_standing_render import RULES_BUDGET_B

    assert authority.HOUSE_RULES_MAX_BYTES == RULES_BUDGET_B == 8192


def _seed_house_rules(sandbox_root: Path, rel: str, body: str) -> None:
    dest = sandbox_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body, encoding="utf-8")


def test_house_rules_over_budget_nongrowing_write_lands(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    over_body = _house_rules_body_exactly(8817)
    smaller = _house_rules_body_exactly(8500)
    _seed_house_rules(sandbox_root, rel, over_body)
    current = paths.sha256_of_file(sandbox_root / rel)
    landed = ops_text.write_file_impl(
        rel,
        smaller,
        expected_sha256=current,
        artifact_class="shared",
    )
    assert landed["status"] == "written"
    assert (sandbox_root / rel).read_text(encoding="utf-8") == smaller


def test_house_rules_over_budget_equal_size_lands(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    body = _house_rules_body_exactly(8817)
    _seed_house_rules(sandbox_root, rel, body)
    current = paths.sha256_of_file(sandbox_root / rel)
    landed = ops_text.write_file_impl(
        rel,
        body,
        expected_sha256=current,
        artifact_class="shared",
    )
    assert landed["status"] == "written"


def test_house_rules_over_budget_growth_refused_file_unchanged(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    under = _house_rules_body_exactly(8817)
    _seed_house_rules(sandbox_root, rel, under)
    before = (sandbox_root / rel).read_bytes()
    current = paths.sha256_of_file(sandbox_root / rel)
    rejected = ops_text.write_file_impl(
        rel,
        under + "X",
        expected_sha256=current,
        artifact_class="shared",
    )
    _assert_rotation_refusal(rejected)
    assert (sandbox_root / rel).read_bytes() == before


def test_house_rules_under_budget_growth_past_8192_refused(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    body = _house_rules_body_exactly(8100)
    _seed_house_rules(sandbox_root, rel, body)
    before = (sandbox_root / rel).read_bytes()
    current = paths.sha256_of_file(sandbox_root / rel)
    rejected = ops_text.write_file_impl(
        rel,
        body + ("y" * 200),
        expected_sha256=current,
        artifact_class="shared",
    )
    _assert_rotation_refusal(rejected)
    assert (sandbox_root / rel).read_bytes() == before


def test_rulings_archive_over_8192_ungated(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-rulings-archive.md"
    big = "# archive\n" + ("a" * 9000) + "\n"
    written = ops_text.write_file_impl(rel, big)
    assert written["status"] == "written"
    assert len((sandbox_root / rel).read_bytes()) > authority.HOUSE_RULES_MAX_BYTES


@pytest.mark.parametrize(
    ("operation", "edit_content", "edit_kwargs"),
    [
        ("append", "Z" * 64, {}),
        ("prepend", "Z" * 64, {}),
        ("replace", "SENTINELX", {"target": "SENTINEL"}),
        ("insert_at_line", "insert\n", {"line": 2}),
    ],
)
def test_house_rules_edit_over_budget_growth_refused_bytes_unchanged(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    edit_content: str,
    edit_kwargs: dict,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(ops_text, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    if operation == "replace":
        intro = "## HOUSE RULES\n"
        body = intro + "x" * (8192 - len(intro.encode("utf-8")) - 8) + "SENTINEL"
        assert len(body.encode("utf-8")) == 8192
    else:
        body = _house_rules_body_exactly(8817)
    _seed_house_rules(sandbox_root, rel, body)
    before = (sandbox_root / rel).read_bytes()
    current = paths.sha256_of_file(sandbox_root / rel)
    rejected = ops_text.edit_file_impl(
        rel,
        operation,
        edit_content,
        expected_sha256=current,
        artifact_class="shared",
        **edit_kwargs,
    )
    _assert_rotation_refusal(rejected)
    assert (sandbox_root / rel).read_bytes() == before


def test_ops_text_passes_post_edit_image_and_surfaces_size_rejection(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan §6 name for the replace case: small fragment, post-edit image grows."""
    test_house_rules_edit_over_budget_growth_refused_bytes_unchanged(
        sandbox_root,
        monkeypatch,
        "replace",
        "SENTINELX",
        {"target": "SENTINEL"},
    )


def test_house_rules_create_over_8192_refused(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/new-house-rules.md"
    rejected = ops_text.write_file_impl(rel, "x" * 8193)
    assert rejected.get("reason") == "house_rules.over_budget"
    assert not (sandbox_root / rel).exists()


def test_edit_file_surfaces_unknown_authority_reason(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(ops_text, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    _seed_house_rules(sandbox_root, rel, _house_rules_body_exactly(100))
    before = (sandbox_root / rel).read_bytes()

    def future(**_kwargs):
        return {
            "reason": "future.reason",
            "error": "later gate",
            "artifact_class": "shared",
        }

    monkeypatch.setattr(ops_text, "evaluate_write_authority", future)
    rejected = ops_text.edit_file_impl(rel, "append", "Z")
    assert rejected.get("reason") == "future.reason"
    assert (sandbox_root / rel).read_bytes() == before


def test_edit_project_file_house_rules_growth_returns_structured_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import project

    monkeypatch.setattr(project, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(project, "record", lambda *_a, **_k: None)
    target = tmp_path / "x-house-rules.md"
    target.write_text("x" * 8300, encoding="utf-8")
    before = target.read_bytes()
    captured: dict = {}

    class Capture:
        def tool(self, **_kwargs):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn

            return deco

    project.register_project_tools(Capture())
    rejected = captured["edit_project_file"]("x-house-rules.md", "append", "Z")
    assert rejected.get("reason") == "house_rules.over_budget"
    assert target.read_bytes() == before


def test_edit_context_file_house_rules_growth_returns_structured_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.context import context_file_mutations as mutations
    from tools.context import tasks_path_policy

    monkeypatch.setattr(tasks_path_policy, "TASKS_ROOT", tmp_path)
    monkeypatch.setattr(mutations, "record", lambda *_a, **_k: None)
    target = tmp_path / "x-house-rules.md"
    target.write_text("x" * 8300, encoding="utf-8")
    before = target.read_bytes()
    captured: dict = {}

    class Capture:
        def tool(self, **_kwargs):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn

            return deco

    mutations.register_context_file_mutation_tools(Capture())
    rejected = captured["edit_context_file"]("x-house-rules.md", "append", "Z")
    assert rejected.get("reason") == "house_rules.over_budget"
    assert target.read_bytes() == before


def test_file_editor_house_rules_growth_refused(
    sandbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops_write, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(ops_text, "record", lambda *_a, **_k: None)
    rel = "notes/system/threads/12286-house-rules.md"
    body = _house_rules_body_exactly(8817)
    _seed_house_rules(sandbox_root, rel, body)
    before = (sandbox_root / rel).read_bytes()
    current = paths.sha256_of_file(sandbox_root / rel)
    rejected = ops_text.edit_file_impl(
        rel,
        "append",
        "Z",
        expected_sha256=current,
        artifact_class="shared",
    )
    _assert_rotation_refusal(rejected)
    assert (sandbox_root / rel).read_bytes() == before


def test_file_editor_and_surface_registration_import_order_no_cycle() -> None:
    """Importing tools.context / tools.file_editor before tools.filesystem must not circular-import."""
    import subprocess
    import sys

    mcp_dir = str(Path(__file__).resolve().parents[2])
    for stmt in (
        "import tools.file_editor",
        "import tools.context",
        "import surface_registration",
    ):
        proc = subprocess.run(
            [sys.executable, "-c", stmt],
            cwd=mcp_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"{stmt!r} failed:\n{proc.stderr}"

