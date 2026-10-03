"""Hermetic filters for the Customize Yours card list."""

from __future__ import annotations

import pytest

from claude_bundles.skills_ui_yours import slugs_from_card_labels, yours_created_slugs


def test_card_labels_drop_count_badge_and_keep_slugs() -> None:
    labels = ["55", "directive-authoring-standard", "Advisor Timing", "fs", "by you"]
    assert slugs_from_card_labels(labels) == {
        "directive-authoring-standard",
        "fs",
    }


class _HeadingWait:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls = 0

    async def wait_for(self, **_kwargs: object) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


class _FakePage:
    """evaluate returns canned values; get_by_role is the heading wait."""

    def __init__(
        self,
        results: list[object],
        wait: _HeadingWait | None = None,
    ) -> None:
        self._results = list(results)
        self.wait = wait or _HeadingWait()

    async def evaluate(self, _script: str) -> object:
        return self._results.pop(0)

    def get_by_role(self, role: str, *, name: str = "") -> _HeadingWait:
        assert role == "heading"
        assert name == "Created by you"
        return self.wait


@pytest.mark.asyncio
async def test_yours_absent_radio_returns_none() -> None:
    page = _FakePage(["absent"])
    assert await yours_created_slugs(page) is None  # type: ignore[arg-type]
    assert page.wait.calls == 0


@pytest.mark.asyncio
async def test_yours_clicked_heading_timeout_raises() -> None:
    page = _FakePage(["clicked"], wait=_HeadingWait(TimeoutError("heading")))
    with pytest.raises(TimeoutError, match="heading"):
        await yours_created_slugs(page)  # type: ignore[arg-type]
    assert page.wait.calls == 1


@pytest.mark.asyncio
async def test_yours_checked_labels_return_slugs() -> None:
    page = _FakePage(["checked", ["55", "directive-authoring-standard", "fs"]])
    assert await yours_created_slugs(page) == {  # type: ignore[arg-type]
        "directive-authoring-standard",
        "fs",
    }
    assert page.wait.calls == 0
