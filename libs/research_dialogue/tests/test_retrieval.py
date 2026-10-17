from __future__ import annotations

import pytest

from research_dialogue.retrieval import search

pytestmark = pytest.mark.offline


def test_allowlist_drop() -> None:
    res = search("life_legal", "query", client_factory=lambda: _Fake())
    assert res.dropped is True
    assert res.drop_reason == "not on allowlist"


class _Fake:
    def __enter__(self):
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def post(self, path: str, *, json: dict) -> object:
        raise AssertionError("should not POST for dropped scope")
