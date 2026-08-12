"""Attach verification reads the DOM, not the clicker's account of itself."""

from __future__ import annotations

import pytest

from claude_bundles import composer_session_skills as css
from claude_bundles.composer_skill_observe import (
    attach_skills_verified,
    observe_composer_skill_chips,
)
from claude_bundles.cowork_skill_delivery import SkillDeliveryError

pytestmark = pytest.mark.offline
asyncio_test = pytest.mark.asyncio


class FakePage:
    """Serves canned ``evaluate`` payloads in order; last payload repeats."""

    def __init__(self, payloads: list[dict]):
        self._payloads = list(payloads)
        self.url = "https://claude.ai/new"
        self.evaluate_calls = 0

    async def evaluate(self, _js, *_args):
        self.evaluate_calls += 1
        if len(self._payloads) > 1:
            return self._payloads.pop(0)
        return self._payloads[0]

    async def wait_for_timeout(self, _ms):
        return None


def chips(*slugs: str) -> dict:
    return {"ok": True, "slugs": list(slugs)}


@pytest.fixture
def record_attaches(monkeypatch):
    calls: list[str] = []

    async def _attach(page, slug, *, composer):  # noqa: ARG001
        calls.append(slug)

    monkeypatch.setattr(css, "attach_one_session_skill", _attach)
    return calls


@asyncio_test
async def test_observe_reads_slugs_from_chip_payload():
    page = FakePage([chips("reasoning-posture", "consult-posture")])
    assert await observe_composer_skill_chips(page) == [
        "reasoning-posture",
        "consult-posture",
    ]


@asyncio_test
async def test_observe_raises_when_composer_unreadable():
    page = FakePage([{"ok": False, "reason": "composer_missing", "slugs": []}])
    with pytest.raises(SkillDeliveryError, match="composer_missing"):
        await observe_composer_skill_chips(page)


@asyncio_test
async def test_already_attached_skips_clicking(record_attaches):
    page = FakePage([chips("reasoning-posture")])
    obs = await attach_skills_verified(page, ["reasoning-posture"], composer=object())
    assert obs.complete
    assert obs.missing == ()
    assert record_attaches == []


@asyncio_test
async def test_retries_only_the_missing_slug(record_attaches):
    page = FakePage(
        [
            chips("reasoning-posture"),
            chips("reasoning-posture", "consult-posture"),
        ]
    )
    obs = await attach_skills_verified(
        page,
        ["reasoning-posture", "consult-posture"],
        composer=object(),
    )
    assert obs.complete
    assert record_attaches == ["consult-posture"]
    assert obs.attempts == 2


@asyncio_test
async def test_never_landing_slug_is_reported_missing(record_attaches):
    page = FakePage([chips("reasoning-posture")])
    obs = await attach_skills_verified(
        page,
        ["reasoning-posture", "consult-posture"],
        composer=object(),
        attempts=2,
    )
    assert not obs.complete
    assert obs.missing == ("consult-posture",)
    assert obs.observed == ("reasoning-posture",)
    assert record_attaches == ["consult-posture"] * 2


@asyncio_test
async def test_clicker_success_without_a_chip_is_not_delivery(monkeypatch):
    """The regression: attach returns cleanly, page shows nothing."""

    async def _attach(page, slug, *, composer):  # noqa: ARG001
        return None

    monkeypatch.setattr(css, "attach_one_session_skill", _attach)
    page = FakePage([chips()])
    obs = await attach_skills_verified(
        page, ["reasoning-posture"], composer=object(), attempts=2
    )
    assert obs.missing == ("reasoning-posture",)


@asyncio_test
async def test_attach_failure_does_not_abort_remaining_slugs(monkeypatch):
    attempted: list[str] = []

    async def _attach(page, slug, *, composer):  # noqa: ARG001
        attempted.append(slug)
        if slug == "reasoning-posture":
            raise SkillDeliveryError("not in Skills list")

    monkeypatch.setattr(css, "attach_one_session_skill", _attach)
    page = FakePage([chips(), chips("consult-posture")])
    obs = await attach_skills_verified(
        page,
        ["reasoning-posture", "consult-posture"],
        composer=object(),
        attempts=1,
    )
    assert "consult-posture" in attempted
    assert obs.missing == ("reasoning-posture",)


@asyncio_test
async def test_empty_request_is_a_no_op(record_attaches):
    page = FakePage([chips()])
    obs = await attach_skills_verified(page, [], composer=object())
    assert obs.as_dict()["requested"] == []
    assert page.evaluate_calls == 0
    assert record_attaches == []


def test_slug_matchers_do_not_select_a_superstring_entry():
    matchers = css._slug_matchers("reasoning-posture")
    assert not any(m.search("meta-reasoning-posture-draft") for m in matchers)
    assert any(m.search("reasoning-posture") for m in matchers)
