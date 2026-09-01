"""Regression tests for packet file-path extraction (errno-36 over-capture).

Root cause (thread 2677): the inline-backtick scan in ``_files_from_packet`` used
``re.findall(r"`([^`]+)`", text)``, which captures across newlines and therefore
mined whole fenced code-block bodies. Slash-bearing prose inside those bodies
(e.g. "worker/coord") passed the too-loose ``_looks_like_file_path`` and became a
bogus ``files_expected`` entry, crashing closeout with OSError ENAMETOOLONG.
"""

from __future__ import annotations

from implement_admission.normalize import (
    _files_from_packet,
    _looks_like_file_path,
)


def test_looks_like_file_path_accepts_real_paths() -> None:
    assert _looks_like_file_path("a/b.py")
    assert _looks_like_file_path("foo.md")
    assert _looks_like_file_path("libs/implement_admission/normalize.py")


def test_looks_like_file_path_rejects_non_path_shapes() -> None:
    assert not _looks_like_file_path("")
    assert not _looks_like_file_path("   ")
    assert not _looks_like_file_path("worker/coord split on active arc")  # whitespace
    assert not _looks_like_file_path("line one/two\nline three")  # newline
    assert not _looks_like_file_path("x/" + "a" * 300)  # overlength


def test_looks_like_file_path_rejects_uri_schemes() -> None:
    assert not _looks_like_file_path("cortex://notes/system/threads/x.md")
    assert not _looks_like_file_path("workspaces://universal-llm-gateway/foo.py")
    assert not _looks_like_file_path("https://example.com/a/b")


def test_looks_like_file_path_rejects_absolute_non_repo_tokens() -> None:
    """MCP surface names / mount roots (friction a:31774) have no file suffix."""
    assert not _looks_like_file_path("/mcp/code")
    assert not _looks_like_file_path("/mcp/life")
    assert not _looks_like_file_path("/data/files")


def test_looks_like_file_path_accepts_absolute_suffixed_paths() -> None:
    """A genuine absolute in-repo file still has a recognized suffix."""
    assert _looks_like_file_path(
        "/mnt/torus/projects/universal-llm-gateway/services/foo.py"
    )


def test_files_from_packet_ignores_fenced_code_blocks() -> None:
    packet = """
<scope>
Primary artifacts: `libs/implement_admission/normalize.py`,
`services/git_integration_worker/cursor_sdk_deliverables.py`.
</scope>

<task_guidance>
Here is the offending function inlined in a fence:

```python
def consolidation_split_warning(*, reuse_thread, parent_dispatch_thread_id):
    \"\"\"Advisory when a cursor-sdk generate keeps the worker/coord split on a
    numeric active arc — a sibling worker thread was minted instead of
    consolidating Q/R onto one thread.\"\"\"
    arc = (parent_dispatch_thread_id or "").strip()
    return f"kept the worker/coord split on active arc {arc}"
```
</task_guidance>
"""
    files = _files_from_packet(packet)
    assert files == [
        "libs/implement_admission/normalize.py",
        "services/git_integration_worker/cursor_sdk_deliverables.py",
    ]
    for f in files:
        assert "\n" not in f
        assert " " not in f
        assert len(f) <= 200


def test_files_from_packet_rejects_whitespace_prose_spans() -> None:
    packet = """
<scope>
Primary artifacts: `pkg/mod.py`.
</scope>
<task_guidance>
Mind the `worker/coord split distinction` and the `Q/R consolidation rule`.
</task_guidance>
"""
    assert _files_from_packet(packet) == ["pkg/mod.py"]


def test_files_from_packet_ignores_cortex_uri_in_corpus() -> None:
    packet = """
<scope>
Primary artifacts: `libs/implement_admission/normalize.py`.
</scope>
<corpus>
Sidecar: `cortex://notes/system/threads/x.md`
</corpus>
<task_guidance>
acceptance criteria
1. Do the thing
</task_guidance>
"""
    assert _files_from_packet(packet) == ["libs/implement_admission/normalize.py"]


def test_files_from_packet_ignores_durable_share_uris_in_scope() -> None:
    packet = """
<scope>
Repo paths: `libs/implement_admission/normalize.py`, `libs/implement_admission/test_normalize.py`.
Durable refs: `cortex://notes/system/threads/x.md`, `workspaces://universal-llm-gateway/foo.py`.
</scope>
<task_guidance>
acceptance criteria
1. Do the thing
</task_guidance>
"""
    assert _files_from_packet(packet) == [
        "libs/implement_admission/normalize.py",
        "libs/implement_admission/test_normalize.py",
    ]


def test_files_from_packet_ignores_absolute_surface_names_in_prose() -> None:
    """Repro shape from friction a:31774 (evidence: tmp/prompts/md-ops-all-sandboxes.md).

    Backticking MCP surface names / mount roots without a file suffix must not
    poison the derived scope — this is what tripped CURSOR_LANE_B_SCOPE_REFUSED.
    """
    packet = """
<scope>
Repo paths: `services/mcp-server/tools/filesystem/_fs_dispatch.py`.
</scope>
<task_guidance>
Use `fs` with the `/mcp/code` surface, not `/mcp/life`. The cortex share
root is `/data/files`.
</task_guidance>
"""
    assert _files_from_packet(packet) == [
        "services/mcp-server/tools/filesystem/_fs_dispatch.py",
    ]


def test_files_from_packet_frontmatter_files_expected_is_authoritative() -> None:
    """An explicit front-matter list wins outright — the scrape never runs."""
    packet = """---
contract: implement
files_expected:
  - services/mcp-server/tools/filesystem/_fs_dispatch.py
  - services/mcp-server/fs_roots.py
---

<scope>
Use `/mcp/code` and `/mcp/life`; a poisoning absolute token that would
otherwise never matter since front matter is authoritative.
</scope>
"""
    assert _files_from_packet(packet) == [
        "services/mcp-server/tools/filesystem/_fs_dispatch.py",
        "services/mcp-server/fs_roots.py",
    ]


def test_files_from_packet_frontmatter_empty_list_yields_empty_scope() -> None:
    """An explicit empty front-matter list is a deliberate bind-only scope, not a fallback trigger."""
    packet = """---
contract: light-bounded
files_expected:
---

<scope>
Primary artifacts: `libs/implement_admission/normalize.py`.
</scope>
"""
    assert _files_from_packet(packet) == []


def test_files_from_packet_no_frontmatter_falls_back_to_scrape() -> None:
    """Without front matter, today's scope-block + backtick-scrape behavior is unchanged."""
    packet = """
<scope>
Primary artifacts: `libs/implement_admission/normalize.py`.
</scope>
"""
    assert _files_from_packet(packet) == ["libs/implement_admission/normalize.py"]
