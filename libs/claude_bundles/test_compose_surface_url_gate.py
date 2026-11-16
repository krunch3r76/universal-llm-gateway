"""Hermetic URL gate for skills-attach compose surfaces."""

from __future__ import annotations

import pytest

from claude_bundles.composer_session_skills import (
    project_shell_compose_url,
    url_is_skills_compose_surface,
)

_UUID = "01a1d84b-20a4-7297-8271-1fc41d25982d"


@pytest.mark.offline
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://claude.ai/new", True),
        ("https://claude.ai/new?foo=1", True),
        (
            f"https://claude.ai/cowork/project/{_UUID}",
            True,
        ),
        ("https://claude.ai/cowork/cse_abc", True),
        ("https://claude.ai/chat/abc-123", True),
        ("https://claude.ai/code", True),
        (f"https://claude.ai/cowork/project/{_UUID}/new", False),
        ("https://claude.ai/cowork/projects", False),
        ("https://claude.ai/project/abc", False),
        ("https://example.com/new", False),
    ],
)
def test_url_is_skills_compose_surface(url: str, expected: bool) -> None:
    assert url_is_skills_compose_surface(url) is expected


@pytest.mark.offline
def test_project_shell_compose_url_rejects_extra_segments() -> None:
    base = f"https://claude.ai/cowork/project/{_UUID}"
    assert project_shell_compose_url(base) is True
    assert project_shell_compose_url(f"{base}/new") is False