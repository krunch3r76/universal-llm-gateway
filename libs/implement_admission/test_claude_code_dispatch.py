"""Tests for claude_code_dispatch route-policy toggle."""

from __future__ import annotations

import pytest

from implement_admission.claude_code_dispatch import (
    claude_code_dispatch_prohibited,
    is_claude_code_surface,
    load_claude_code_dispatch_mode,
)


def test_default_prohibit_when_key_absent(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
  policy = tmp_path / "route_policy.yaml"
  policy.write_text("policy_source: test\n", encoding="utf-8")
  assert load_claude_code_dispatch_mode(policy) == "prohibit"
  assert claude_code_dispatch_prohibited(policy)


def test_allow_mode(tmp_path) -> None:
  policy = tmp_path / "route_policy.yaml"
  policy.write_text(
    "policy_source: test\nclaude_code_dispatch: allow\n",
    encoding="utf-8",
  )
  assert load_claude_code_dispatch_mode(policy) == "allow"
  assert not claude_code_dispatch_prohibited(policy)


def test_invalid_mode_raises(tmp_path) -> None:
  policy = tmp_path / "route_policy.yaml"
  policy.write_text(
    "policy_source: test\nclaude_code_dispatch: maybe\n",
    encoding="utf-8",
  )
  with pytest.raises(ValueError, match="claude_code_dispatch"):
    load_claude_code_dispatch_mode(policy)


@pytest.mark.parametrize(
  "surface,expected",
  [
    ("code", True),
    ("CODE", True),
    ("cowork", False),
    (None, False),
    ("", False),
  ],
)
def test_is_claude_code_surface(surface: str | None, expected: bool) -> None:
  assert is_claude_code_surface(surface) is expected
