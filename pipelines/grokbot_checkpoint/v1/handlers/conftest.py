"""In-memory cortex fs and bus fakes."""

from __future__ import annotations

from typing import Any

import pytest


class MemFs:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.writes: list[str] = []

    async def read(self, rel: str) -> bytes | None:
        return self.files.get(rel)

    async def read_uri(self, uri: str) -> bytes | None:
        for prefix in ("cortex://", "workspaces://"):
            if uri.startswith(prefix):
                return self.files.get(uri.removeprefix(prefix))
        return self.files.get(uri)

    async def write(
        self,
        rel: str,
        data: bytes,
        *,
        expected_sha256: str | None = None,
        if_absent: bool = False,
    ) -> dict[str, Any]:
        from ._clients import file_sha256

        current = self.files.get(rel)
        if if_absent and current is not None:
            return {"ok": False, "conflict": True, "sha256": file_sha256(current)}
        current_sha = file_sha256(current) if current is not None else None
        if expected_sha256 is not None and current_sha != expected_sha256:
            return {"ok": False, "conflict": True, "sha256": current_sha}
        self.files[rel] = data
        self.writes.append(rel)
        return {"ok": True, "conflict": False, "sha256": file_sha256(data)}


@pytest.fixture
def fs() -> MemFs:
    return MemFs()
