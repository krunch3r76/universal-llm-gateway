"""Smoke tests for libs/doc_extraction/targeted_refresh.py."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from doc_extraction.targeted_refresh import main, refresh_routing

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCOPE_SOURCE_COMMIT_RE = re.compile(r"source commit `[0-9a-f]{7,40}`")


def _replace_scope_source_commit(text: str, head: str) -> str:
    return _SCOPE_SOURCE_COMMIT_RE.sub(f"source commit `{head}`", text)


def _scope_section(text: str) -> str:
    start = text.index("## Scope of this pass")
    chunk = text[start:]
    end = chunk.index("\n---")
    return chunk[:end]


def test_main_usage_returns_2() -> None:
    assert main([]) == 2
    assert main(["routing"]) == 2
    assert main(["gateway", str(_REPO_ROOT), "extra"]) == 2


def test_refresh_routing_stamp_and_scope_trim() -> None:
    text = refresh_routing(_REPO_ROOT)
    pre_key = text.split("## Key Functions", 1)[0]
    assert "method=targeted-refresh" in pre_key
    scope = _scope_section(pre_key)
    assert "Step 9 subset" not in scope
    assert "Key Functions" in scope
    assert "full production routing tree" in scope


def test_scope_source_commit_substitution_replaces_arbitrary_prior() -> None:
    prior = "0d45bbef52c8"
    new_head = "abc123def456"
    scope_line = (
        f"**Targeted refresh** against routing at source commit `{prior}`. "
        "Inventory unchanged."
    )
    out = _replace_scope_source_commit(scope_line, new_head)
    assert f"source commit `{new_head}`" in out
    assert prior not in out


def test_scope_source_commit_substitution_idempotent() -> None:
    text = "at source commit `c75e3fc8`. prose."
    head = "254be89cb7ee"
    once = _replace_scope_source_commit(text, head)
    twice = _replace_scope_source_commit(once, head)
    assert once == twice
    assert f"source commit `{head}`" in twice


def test_scope_source_commit_substitution_preserves_surrounding_text() -> None:
    prior = "0d45bbef52c8"
    new_head = "deadbeefcafe"
    scope_line = (
        f"**Targeted refresh** (Kaywan) at source commit `{prior}`. "
        f"**full module inventory (57/57)**"
    )
    out = _replace_scope_source_commit(scope_line, new_head)
    assert out == scope_line.replace(
        f"source commit `{prior}`",
        f"source commit `{new_head}`",
    )


def test_refresh_routing_scope_source_commit_matches_git_head() -> None:
    head = (
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            text=True,
        )
        .strip()[:12]
    )
    text = refresh_routing(_REPO_ROOT)
    scope = _scope_section(text.split("## Key Functions", 1)[0])
    assert f"source commit `{head}`" in scope
