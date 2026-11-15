"""Table tests for ``parse_claude_seat_address`` (D1)."""

from __future__ import annotations

import pytest

from claude_bundles.cse_url import ClaudeSeatAddress, parse_claude_seat_address

_UUID = "2762063d-2966-4859-a3ce-23831c6e0c86"
_CSE = "cse_01CodB7tom1281iY8BmZJcZM"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            f"https://claude.ai/chat/{_UUID}",
            ClaudeSeatAddress(
                canonical_url=f"https://claude.ai/chat/{_UUID}",
                kind="unified_chat",
                primary_id=_UUID,
            ),
        ),
        (
            f"https://www.claude.ai/chat/{_UUID}",
            ClaudeSeatAddress(
                canonical_url=f"https://claude.ai/chat/{_UUID}",
                kind="unified_chat",
                primary_id=_UUID,
            ),
        ),
        (
            f"https://claude.ai/chat/{_UUID}/",
            ClaudeSeatAddress(
                canonical_url=f"https://claude.ai/chat/{_UUID}",
                kind="unified_chat",
                primary_id=_UUID,
            ),
        ),
        (
            f"https://claude.ai/chat/{_UUID}?foo=1",
            ClaudeSeatAddress(
                canonical_url=f"https://claude.ai/chat/{_UUID}",
                kind="unified_chat",
                primary_id=_UUID,
            ),
        ),
        (
            f"https://claude.ai/chat/{_UUID}#x",
            ClaudeSeatAddress(
                canonical_url=f"https://claude.ai/chat/{_UUID}",
                kind="unified_chat",
                primary_id=_UUID,
            ),
        ),
        (
            "https://claude.ai/chat/2762063D-2966-4859-A3CE-23831C6E0C86",
            ClaudeSeatAddress(
                canonical_url=f"https://claude.ai/chat/{_UUID}",
                kind="unified_chat",
                primary_id=_UUID,
            ),
        ),
        (
            f"https://claude.ai/cowork/{_CSE}",
            ClaudeSeatAddress(
                canonical_url=f"https://claude.ai/cowork/{_CSE}",
                kind="cowork_cse",
                primary_id=_CSE,
            ),
        ),
        (
            _CSE,
            ClaudeSeatAddress(
                canonical_url=f"https://claude.ai/cowork/{_CSE}",
                kind="cowork_cse",
                primary_id=_CSE,
            ),
        ),
        (
            "https://claude.ai/code/session_abc123",
            ClaudeSeatAddress(
                canonical_url="https://claude.ai/code/session_abc123",
                kind="code_session",
                primary_id="session_abc123",
            ),
        ),
    ],
)
def test_accepts_table(raw: str, expected: ClaudeSeatAddress) -> None:
    got = parse_claude_seat_address(raw)
    assert got == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://claude.ai/chat/not-a-uuid",
        "https://claude.ai/new",
        "https://claude.ai/code",
        "https://claude.ai/",
        f"http://claude.ai/chat/{_UUID}",
        f"https://evil.example/chat/{_UUID}",
        f"https://claude.ai.evil.example/chat/{_UUID}",
        "",
        "junk",
        "cse_",
    ],
)
def test_refuses_table(raw: str) -> None:
    assert parse_claude_seat_address(raw) is None
    assert parse_claude_seat_address(None) is None


def test_linked_cse_token_never_inferred() -> None:
    parsed = parse_claude_seat_address(f"https://claude.ai/chat/{_UUID}")
    assert parsed is not None
    assert parsed.linked_cse_token is None