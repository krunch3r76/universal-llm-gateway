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
