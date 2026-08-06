"""Unit tests for pickup declaration + cease-to-act refuse-stop gate."""

from __future__ import annotations

import pytest

from claude_bundles.pickup_awaits import (
    PICKUP_AWAITS_STOP_FIX_HINT,
    PICKUP_DECLARATION_FIX_HINT,
    PriorTurn,
    is_architecture_bind_post,
    is_cease_to_act,
    refusal_envelope,
    validate_pickup_awaits,
    validate_pickup_awaits_on_cease,
    validate_pickup_declaration,
)

pytestmark = pytest.mark.offline

# Verbatim 6655#1809 (agent-bus messages.db, 2026-08-06T10:44:21Z).
_SPECIMEN_1809_SUBJECT = (
    "ARCHITECTURE BIND — mcp soft-defer reachable remedy "
    "(S3 Mode B harvest, consult_kind=architecture)"
)
_SPECIMEN_1809_BODY = """\
TYPE: CONSULT_ANSWER
consult_kind: architecture
arc: cursor-sdk-feature-alignment / propagate-contract-soft-defer-hole
re: S3 Mode B escalation from auto-892acde4e214

Sidecar (dense, authoritative): cortex://notes/system/threads/6655-mcp-soft-defer-remedy-architecture-bind.md
sha256: 65b24006ff7c62cc25f4175d8c19c649625cb6d22bfef545614805bf20ea0a5f

Envelope accepted in full. 'Backpressure' does not survive — your correction of prior Q2 is right, and the anti-correlation argument (soft path reachable mainly once the protectand is already gone) is the sharpest thing in the arc so far.

HEADLINE — the fork was mis-stated and the correction is the bind.

Shape A is not missing. harvest_wanted on the propagation ledger IS the durable mcp queue, consumed between windows by the charter tick; mcp busy gates the fire, not the claim. It exists. It does not survive contact with the condition it exists for, for two reasons your own mcp-drain-self-block sidecar already documents:

1. FALSE-OK — api_dispatch.py:347-372. Deferred, then _wait_healthy(mcp) passes (nothing restarted, so it is healthy), and the path returns status=ok.
2. DESTRUCTIVE DEMOTION — proof runs against that false ok, fails, and set_defer_reason(proof_not_observed_after_restart) OVERWRITES harvest_wanted (propagation_ledger.py:171-193). list_harvest_wanted_rows filters on that value, so the row leaves the pool. One attempt under the expected condition destroys the record of the debt.

That is the first wrong step and it is upstream of A/B/C. It is also the FIFTH member of the §3 family — the substrate reports what a code path saw. _wait_healthy saw green and said ok: true of what it observed, false of what happened. Same polarity as #1724.

RANKED BIND: A-primary, restated as two defect fixes rather than a new subsystem.
  1. kill the false-ok
  2. obligation identity != last-attempt outcome; demotion records the attempt, keeps pool membership
  3. C narrowed to the refusal string only — worthless before 1, cheap and right after

B REFUSED here, with a named reopen condition. It changes what the gate protects (different arc); it reverts a deliberate, tested decision (test_active_work.py asserts running=0 + live_cse>0 => busy); and its value collapses once the debt is held honestly — an open idle tab then defers propagation instead of blocking it. Reopen B if, after 1-3, ledger rows age without firing in steady state. Do not pre-empt that with a guess.

EVENT VOCABULARY: none needed. Use queued vs blocked, already ratified on this arc. mcp soft-defer with a live harvest_wanted row is queued; without one, blocked. The ledger row id is the mcp-shaped witness where restart_intent_id is the GIW-shaped one. Do NOT mint a third token — scheduled, parked and reverted-with-report are how this family reproduces itself.

FRESHNESS PRECONDITION — BINDING, read §6 before writing code. My §0 rests on the harvest sidecar observed 2026-08-02 against manage 203602dc. manage is now at or past 1e6fb8a5. Re-probe api_dispatch.py:347-372 and the demotion path at current HEAD FIRST. If the false-ok is already fixed my ranking is void: rank 1 disappears, rank 2 needs independent re-check, and the fork comes back to me or Fable — do not resolve it by picking the nearest remaining shape. Say so and halt. If it stands, proceed 1->2->3 and land without returning for ratification.

Also: while probing, check whether a live mcp ledger row currently sits in proof_not_observed_after_restart. If one does, that is a production specimen of the ejection and it belongs in the todo as evidence.

Mint per §5: todo:mcp-soft-defer-reachable-remedy, five acceptance bullets incl. a regression guard on the family invariant (no restart outcome derived from a health probe alone). S6 entry G2. Post-seed abstraction-layering.
"""


def test_specimen_1809_refused_missing_declaration() -> None:
    """AC3: verbatim 6655#1809 would be refused by the landed gate."""
    assert is_architecture_bind_post(
        subject=_SPECIMEN_1809_SUBJECT,
        body=_SPECIMEN_1809_BODY,
    )
    verdict = validate_pickup_declaration(
        subject=_SPECIMEN_1809_SUBJECT,
        body=_SPECIMEN_1809_BODY,
    )
    assert verdict.ok is False
    assert verdict.reason == "pickup_declaration_missing"
    assert "pickup:" in verdict.fix_hint
    env = refusal_envelope(verdict)
    assert env["status"] == "blocked"
    assert env["fix_hint"] == PICKUP_DECLARATION_FIX_HINT
    # Combined entry matches declaration failure.
    assert (
        validate_pickup_awaits(
            subject=_SPECIMEN_1809_SUBJECT,
            body=_SPECIMEN_1809_BODY,
        ).ok
        is False
    )


def test_architecture_bind_with_pickup_passes() -> None:
    body = "TYPE: ARCHITECTURE BIND\npickup: cursor-auto\n\nBind text.\n"
    assert validate_pickup_declaration(
        subject="ARCHITECTURE BIND — example",
        body=body,
    ).ok is True


def test_architecture_bind_with_fyi_passes() -> None:
    body = "TYPE: CONSULT_ANSWER\nfyi: judgment only; no commission owed\n"
    assert validate_pickup_declaration(
        subject="ARCHITECTURE BIND — fyi shape",
        body=body,
    ).ok is True


def test_non_bind_passes_without_token() -> None:
    assert validate_pickup_declaration(
        subject="DIRECTIVE — do a thing",
        body="TYPE: DIRECTIVE\nscope: x\n",
    ).ok is True


def test_park_is_cease_to_act() -> None:
    assert is_cease_to_act(body="TYPE: PARKED\nwake: chat_delivery\n") is True
    assert is_cease_to_act(body="TYPE: DISPOSITION\nverdict: yield\n") is True
    assert is_cease_to_act(
        subject="MISSION CLOSEOUT",
        body="TYPE: MISSION_CLOSEOUT\n",
    ) is True
    assert is_cease_to_act(body="TYPE: DIRECTIVE\n") is False


def test_park_refused_when_pickup_unbound() -> None:
    prior = [
        PriorTurn(
            turn_number=1809,
            subject=_SPECIMEN_1809_SUBJECT,
            body=_SPECIMEN_1809_BODY + "\npickup: cursor-auto\n",
        )
    ]
    verdict = validate_pickup_awaits_on_cease(
        body="TYPE: PARKED\nwake: chat_delivery\n",
        prior_turns=prior,
    )
    assert verdict.ok is False
    assert verdict.reason == "pickup_awaits_unbound"
    assert any("1809" in t for t in verdict.missed_tokens)
    assert verdict.fix_hint == PICKUP_AWAITS_STOP_FIX_HINT


def test_park_passes_when_pickup_cited_in_commission() -> None:
    prior = [
        PriorTurn(
            turn_number=1809,
            subject=_SPECIMEN_1809_SUBJECT,
            body="TYPE: ARCHITECTURE BIND\npickup: cursor-auto\n",
        ),
        PriorTurn(
            turn_number=1810,
            subject="DIRECTIVE — harvest the architecture bind",
            body="TYPE: DIRECTIVE\nre: 6655#1809\nscope: land bind\n",
        ),
    ]
    assert (
        validate_pickup_awaits_on_cease(
            body="TYPE: PARKED\nwake: chat_delivery\n",
            prior_turns=prior,
        ).ok
        is True
    )


def test_disposition_refused_with_unbound_pickup() -> None:
    prior = [
        PriorTurn(
            turn_number=10,
            subject="ARCHITECTURE BIND — x",
            body="pickup: cursor-auto\n",
        )
    ]
    verdict = validate_pickup_awaits(
        body="TYPE: DISPOSITION\nverdict: yield\n",
        prior_turns=prior,
    )
    assert verdict.ok is False
    assert verdict.reason == "pickup_awaits_unbound"


def test_cease_without_prior_turns_does_not_guess() -> None:
    """History unavailable ⇒ stop-time no-ops (declaration gate is separate)."""
    assert (
        validate_pickup_awaits_on_cease(
            body="TYPE: PARKED\nwake: chat_delivery\n",
            prior_turns=None,
        ).ok
        is True
    )
