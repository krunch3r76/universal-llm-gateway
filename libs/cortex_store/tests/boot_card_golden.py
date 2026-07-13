"""Legacy boot card golden fixture loader (F2b parity)."""

from __future__ import annotations

from pathlib import Path

_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "boot_skills_claude_web_legacy_card.md"
)
_LEGACY_SOURCE_COMMIT = "273dd5451ebb9124fb17f1b76beded75ab079fb0"
_HEADER_END = "-->"


def legacy_card_golden_bytes() -> str:
    """Body bytes of git-historical render_skills_section output (header stripped)."""
    raw = _FIXTURE.read_text(encoding="utf-8")
    if raw.startswith("<!--"):
        end = raw.index(_HEADER_END) + len(_HEADER_END)
        body = raw[end:].lstrip("\n")
        if body.endswith("\n"):
            body = body[:-1]
        return f"\n{body}"
    return raw


def assert_card_matches_legacy_golden(rendered: str) -> None:
    """Assert card markdown matches frozen legacy golden (not self-referential)."""
    golden = legacy_card_golden_bytes()
    assert rendered == golden, (
        f"card markdown drift vs legacy golden ({_LEGACY_SOURCE_COMMIT}); "
        "update fixture only after intentional render change"
    )
