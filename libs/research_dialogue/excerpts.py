from __future__ import annotations

import hashlib
from pathlib import Path

DELIVERABLE_SHA256 = (
    "24a0d446516959293a0e57eece42108716be5b75c824bd2b65c9d5b5557c9c00"
)

# 1-based inclusive line ranges sent to the model.
EXCERPT_RANGES: tuple[tuple[int, int], ...] = (
    (3, 11),
    (98, 146),
    (150, 160),
    (170, 170),
    (196, 196),
    (198, 198),
    (201, 201),
    (203, 203),
)

FORBIDDEN_LINES = frozenset({166, 197})


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_lines_from_text(lines: list[str]) -> str:
    """Return excerpt text for configured ranges (1-based line numbers)."""
    selected: list[str] = []
    for start, end in EXCERPT_RANGES:
        for n in range(start, end + 1):
            if n in FORBIDDEN_LINES:
                continue
            if 1 <= n <= len(lines):
                selected.append(lines[n - 1].rstrip("\n"))
    return "\n".join(selected)


def read_excerpts(path: str | Path) -> str:
    p = Path(path)
    digest = _file_sha256(p)
    if digest != DELIVERABLE_SHA256:
        raise ValueError(
            f"deliverable sha256 mismatch: expected {DELIVERABLE_SHA256}, got {digest}"
        )
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    return extract_lines_from_text(lines)
