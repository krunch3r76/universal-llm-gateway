"""In-memory cortex fs and bus fakes."""

from __future__ import annotations

from typing import Any

import pytest


class MemFs:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.writes: list[str] = []

    def read(self, rel: str) -> bytes | None:
        return self.files.get(rel)

    def write(
        self, rel: str, data: bytes, *, expected_sha256: str | None = None
    ) -> dict[str, Any]:
        from ._clients import file_sha256

        current = self.files.get(rel)
        current_sha = file_sha256(current) if current is not None else None
        if expected_sha256 is not None and current_sha != expected_sha256:
            return {"ok": False, "conflict": True, "sha256": current_sha}
        self.files[rel] = data
        self.writes.append(rel)
        return {"ok": True, "conflict": False, "sha256": file_sha256(data)}


@pytest.fixture
def fs() -> MemFs:
    return MemFs()
