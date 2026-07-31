"""Tests for fleet-wide --all --replace upload safety gate."""

from __future__ import annotations

import pytest

from claude_bundles.upload_safety import reject_unsafe_replace_all


def test_all_replace_refused_without_force() -> None:
    with pytest.raises(SystemExit) as caught:
        reject_unsafe_replace_all(
            all_=True,
            replace=True,
            force=False,
            error=lambda msg: (_ for _ in ()).throw(SystemExit(msg)),
        )
    assert "--all --replace is refused" in str(caught.value)


def test_all_replace_allowed_with_force() -> None:
    calls: list[str] = []
    reject_unsafe_replace_all(
        all_=True,
        replace=True,
        force=True,
        error=calls.append,
    )
    assert calls == []


@pytest.mark.parametrize(
    ("all_", "replace"),
    [
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_safe_combinations_pass(all_: bool, replace: bool) -> None:
    calls: list[str] = []
    reject_unsafe_replace_all(
        all_=all_,
        replace=replace,
        force=False,
        error=calls.append,
    )
    assert calls == []
