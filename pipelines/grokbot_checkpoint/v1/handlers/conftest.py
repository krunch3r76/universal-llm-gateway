"""In-memory cortex fs and bus fakes."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

FIXTURE_TRANSCRIPT_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def restamp_transcript_id(blob: bytes, transcript_id: str) -> bytes:
    """Return pour bytes with meta.transcript_id replaced (messages unchanged)."""
    import json

    payload = json.loads(blob)
    payload["meta"]["transcript_id"] = transcript_id
    return json.dumps(payload).encode()


class MemFs:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.writes: list[str] = []
        self.dirs: set[str] = set()

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

    async def list(self, rel: str) -> list[str] | None:
        prefix = rel.removeprefix("cortex://").removeprefix("workspaces://").rstrip("/")
        if prefix in self.files:
            return None
        kids = [
            key
            for key in self.files
            if key.startswith(prefix + "/") and "/" not in key[len(prefix) + 1 :]
        ]
        if not kids and not getattr(self, "dirs", set()).__contains__(prefix):
            return None
        return sorted(kids)


@pytest.fixture
def fs() -> MemFs:
    return MemFs()


@pytest.fixture(autouse=True)
def _no_checkpoint_events(monkeypatch):
    from . import post as post_mod
    from . import seal as seal_mod

    posted, sealed = MagicMock(name="posted_spy"), MagicMock(name="sealed_spy")
    monkeypatch.setattr(post_mod, "stargate_continuity_checkpoint_posted", posted)
    monkeypatch.setattr(seal_mod, "stargate_continuity_checkpoint_sealed", sealed)
    yield {"posted": posted, "sealed": sealed}
