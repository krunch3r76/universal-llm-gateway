"""Action enrichment template dry-run tests — salience slice 2."""

from __future__ import annotations

from predicate_form.action_enrichment import (
    dry_run_enrich_assertions,
    enrich_action_predicate_from_claim,
)

_A20701_CLAIM = (
    "WO 956908029 / lower-payment request — DENIED, confirmed 2026-06-26. "
    "On the 2026-06-26 ~12:30 PM Chase Escalations call (case ECW260413-02188, "
    "rep 'Matthew', who reviewed Janet's notes), Chase stated it is unable to "
    "spread the escrow shortage beyond 12 months and that the request for the "
    "lower payment was DENIED."
)

_A7738_CLAIM = (
    "WO #953902037 — Kaywan's request to extend escrow shortage spread beyond "
    "the standard 12-month RESPA floor — was DENIED on the 2026-04-29 Nell Cruz "
    "callback. Nell stated: 'we are unable to spread the escrow shortage over "
    "12 months.'"
)

_ENTITY = "account:chase-mortgage-8787"


def test_ac3_dry_run_a20701_emits_denied_spread_extension() -> None:
    preview = enrich_action_predicate_from_claim(
        _A20701_CLAIM,
        _ENTITY,
        assertion_id=20701,
        observed_at="2026-06-26T19:54:57Z",
        epistemic_state="committed",
    )
    assert preview is not None
    assert preview.predicate_form == "denied(spread_extension, chase, 2026-06-26)"
    assert preview.assertion_id == 20701
    assert preview.epistemic_state == "committed"


def test_ac3_dry_run_a7738_emits_denied_spread_extension() -> None:
    preview = enrich_action_predicate_from_claim(
        _A7738_CLAIM,
        _ENTITY,
        assertion_id=7738,
        observed_at="2026-04-29T17:10:00Z",
        epistemic_state="staged",
    )
    assert preview is not None
    assert preview.predicate_form == "denied(spread_extension, chase, 2026-04-29)"
    assert preview.assertion_id == 7738


def test_dry_run_batch_fixture_rows() -> None:
    rows = [
        {
            "id": 20701,
            "entity_id": _ENTITY,
            "claim": _A20701_CLAIM,
            "observed_at": "2026-06-26T19:54:57Z",
            "review_status": "committed",
        },
        {
            "id": 7738,
            "entity_id": _ENTITY,
            "claim": _A7738_CLAIM,
            "observed_at": "2026-04-29T17:10:00Z",
            "review_status": "staged",
        },
    ]
    previews = dry_run_enrich_assertions(rows)
    forms = {p.assertion_id: p.predicate_form for p in previews}
    assert forms[20701] == "denied(spread_extension, chase, 2026-06-26)"
    assert forms[7738] == "denied(spread_extension, chase, 2026-04-29)"
