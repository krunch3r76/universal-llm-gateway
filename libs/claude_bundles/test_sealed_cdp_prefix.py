"""Tests for shared CDP delivery-prefix peeling."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles.sealed_cdp_prefix import peel_delivery_prefix

pytestmark = pytest.mark.offline


def test_peel_consumes_render_cdp_required_authority() -> None:
    from claude_bundles.cowork_skill_delivery import render_cdp_required_authority

    marker = render_cdp_required_authority(["reasoning-posture", "cdp-operator-proxy"])
    body = "TYPE: DIRECTIVE\n"
    assert peel_delivery_prefix(marker + body) == body


def test_peel_consumes_format_cdp_use_the_lines() -> None:
    from claude_bundles.cowork_skill_delivery import format_cdp_use_the_lines

    use_lines = format_cdp_use_the_lines(["reasoning-posture", "consult-posture"])
    body = "# work\n"
    assert peel_delivery_prefix(use_lines + body) == body


def test_peel_consumes_render_skill_induction_lines() -> None:
    from claude_bundles.cowork_skill_delivery import render_skill_induction

    induction = render_skill_induction(["reasoning-posture"]) + "\n"
    body = "# work\n"
    assert peel_delivery_prefix(induction + body) == body


def test_peel_consumes_render_cdp_inline_skills_xml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles.cowork_skill_delivery import (
        InjectedSkillBody,
        render_cdp_inline_skills_xml,
    )

    repo = tmp_path
    skill_path = repo / "skill.md"
    skill_path.write_text("# skill\n", encoding="utf-8")
    bodies = [
        InjectedSkillBody(
            slug="ulg-architecture",
            surface_class="cursor_only",
            path=skill_path,
            body="# ulg-architecture\n",
        )
    ]
    block = render_cdp_inline_skills_xml(bodies, repo_root=repo)
    body = "TYPE: handoff\n"
    assert peel_delivery_prefix(block + body).lstrip() == body


def test_peel_consumes_format_house_read_first_block_before_briefing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import hashlib

    from agent_bus_store.house_pools import (
        ContinuityCard,
        format_house_read_first_block,
        parse_pools,
    )
    from agent_bus_store.test_house_pools import _MANIFEST_BLOCK

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    card_text = f"# card\n\n{_MANIFEST_BLOCK}\n"
    rel = "notes/system/threads/10223-card.md"
    row = parse_pools(card_text)["fable"]
    card = ContinuityCard(
        status="found",
        tried=(rel,),
        relpath=rel,
        uri=f"cortex://{rel}",
        text=card_text,
        sha256=hashlib.sha256(card_text.encode("utf-8")).hexdigest(),
    )
    house = format_house_read_first_block(house_id="10223", row=row, card=card)
    briefing = "# Hop on agent-bus:10223: you are the operator seat\n\n## Data\n"
    body = "TYPE: CONTINUITY_HANDOFF\n"
    stacked = f"{house}\n\n{briefing}{body}"
    peeled = peel_delivery_prefix(stacked)
    assert peeled.startswith("# Hop on agent-bus:")
    assert body in peeled
    assert f"cortex://{rel}" in house


def test_delivery_footer_is_last_on_review_class_contracts() -> None:
    from claude_bundles.sealed_cdp_prefix import (
        REVIEW_DELIVERY_FOOTER,
        ensure_review_delivery_footer,
    )

    base = "Hash these skills from your local skill server\n\n# Review body\n"
    for contract in ("code-review", "delivery-review", "check-review"):
        merged = ensure_review_delivery_footer(
            base, "review", contract=contract, author_body=base
        )
        assert merged.rstrip().endswith(REVIEW_DELIVERY_FOOTER.strip())


def test_delivery_footer_idempotent_on_restage() -> None:
    from claude_bundles.sealed_cdp_prefix import ensure_review_delivery_footer

    once = ensure_review_delivery_footer(
        "Hash these skills\n\nbody\n", "review", contract="code-review"
    )
    twice = ensure_review_delivery_footer(
        once, "review", contract="code-review", author_body=once
    )
    assert once == twice


def test_delivery_footer_skips_skeptic_shaped_body() -> None:
    from claude_bundles.sealed_cdp_prefix import ensure_review_delivery_footer

    skeptic = "You are the G4 skeptic seat for this packet.\n"
    assert (
        ensure_review_delivery_footer(
            skeptic, "review", contract="code-review", author_body=skeptic
        )
        == skeptic
    )


def test_delivery_footer_absent_on_non_review_contract() -> None:
    from claude_bundles.sealed_cdp_prefix import ensure_review_delivery_footer

    body = "freeform prompt\n"
    assert ensure_review_delivery_footer(body, None, contract="confer") == body


def test_delivery_footer_verdict_template_matches_canonical_regex() -> None:
    import re

    from claude_bundles.sealed_cdp_prefix import REVIEW_DELIVERY_FOOTER

    # Owner: cdp_onbehalf_delivery.py CANONICAL_VERDICT_RE
    pattern = re.compile(
        r"(?im)^VERDICT:[ \t]+(APPROVE|REQUEST_CHANGES)[ \t]+"
        r"range=([0-9a-f]{40})\.\.([0-9a-f]{40})[ \t]+head=\3"
        r"(?:[ \t]+fixes=([0-9a-f]{40}))?[ \t]*$"
    )
    base = "a" * 40
    head = "b" * 40
    sample = (
        f"VERDICT: APPROVE range={base}..{head} head={head}"
    )
    assert pattern.match(sample)
    assert "VERDICT:" in REVIEW_DELIVERY_FOOTER
