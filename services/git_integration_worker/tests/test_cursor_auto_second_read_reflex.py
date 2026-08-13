"""Second-read reflex — knob composition, firing predicate, packet parse/inject.

These units are where a bug turns into unmetered premium spend (a predicate that
fires on every closeout) or into a corrupted relay (an injection that eats the
executor's own §2 payload), so they are covered directly rather than through the
async dispatch path.
"""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_auto.reflex_packet import (
    MAX_SECOND_READ_CHARS,
    SECOND_READ_BEGIN,
    SECOND_READ_END,
    build_reflex_packet,
    inject_second_read_block,
    parse_second_read,
    scrub_reserved_status,
)
from services.git_integration_worker.cursor_auto.reflex_policy import (
    counters,
    evaluate_reflex,
)
from services.git_integration_worker.cursor_auto.reflex_read import (
    _DEFAULT_MODEL,
    reflex_model,
)
from services.git_integration_worker.cursor_auto.wire_map import compose_model_knobs

THREAD = "t-reflex"


@pytest.fixture(autouse=True)
def _clean_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    counters().reset()
    monkeypatch.setenv("CURSOR_AUTO_REFLEX_ENABLED", "true")
    monkeypatch.setenv("CURSOR_AUTO_REFLEX_BUDGET", "3")
    # Sampling off by default so trigger tests assert the trigger, not the Nth job.
    monkeypatch.setenv("CURSOR_AUTO_REFLEX_SAMPLE_EVERY", "0")


def _clean_body(status: str = "complete") -> str:
    return (
        "TYPE: CLOSEOUT\n"
        f"status: {status}\n"
        "ac_verdict: AC1=pass AC2=pass\n"
        "effects: edited services/foo/bar.py\n"
        "open forks: none\n"
        "checkpoint: committed abc123 paths=1\n"
    )


# --- default model ----------------------------------------------------------


def test_default_reflex_model_is_grok_not_opus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURSOR_AUTO_REFLEX_MODEL", raising=False)
    assert _DEFAULT_MODEL == "cursor/grok-4.6"
    assert reflex_model() == "cursor/grok-4.6"


def test_reflex_model_env_override_still_honors_opus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CURSOR_AUTO_REFLEX_MODEL", "cursor/claude-opus-5")
    assert reflex_model() == "cursor/claude-opus-5"


# --- knob composition -------------------------------------------------------


def test_effort_merges_onto_opus_and_preserves_base_knobs() -> None:
    knobs = compose_model_knobs(
        {"resolved_model_id": "cursor/claude-opus-5", "model_knobs": {"thinking": "true"}},
        {"resolved_effort": "low"},
    )
    assert knobs == {"thinking": "true", "effort": "low"}


def test_effort_clamps_down_to_model_ceiling() -> None:
    # grok tops out at high; xhigh must degrade rather than drop the knob entirely,
    # which would silently hand the bridge the catalog default.
    knobs = compose_model_knobs(
        {"resolved_model_id": "cursor/grok-4.6"}, {"resolved_effort": "xhigh"}
    )
    assert knobs == {"effort": "high"}


def test_model_without_effort_knob_gets_none() -> None:
    knobs = compose_model_knobs(
        {"resolved_model_id": "cursor/composer-2.5"}, {"resolved_effort": "high"}
    )
    assert knobs == {}


def test_unknown_model_does_not_raise() -> None:
    assert compose_model_knobs(
        {"resolved_model_id": "cursor/not-a-model"}, {"resolved_effort": "low"}
    ) == {}
    assert compose_model_knobs({}, {"resolved_effort": "low"}) == {}


# --- firing predicate -------------------------------------------------------


def test_clean_closeout_does_not_fire() -> None:
    verdict = evaluate_reflex(
        thread_id=THREAD,
        contract="implement",
        terminal_status="completed",
        sdk_body=_clean_body(),
    )
    assert verdict.fire is False
    assert verdict.reason == "no_trigger"


@pytest.mark.parametrize("contract", ["answer", "confer", "execute", "propagate", "seed"])
def test_exempt_contracts_never_fire(contract: str) -> None:
    verdict = evaluate_reflex(
        thread_id=THREAD,
        contract=contract,
        terminal_status="failed",
        sdk_body=_clean_body("partial"),
    )
    assert verdict.fire is False
    assert verdict.reason.startswith("contract_exempt")


def test_disabled_flag_wins_over_every_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_AUTO_REFLEX_ENABLED", "off")
    verdict = evaluate_reflex(
        thread_id=THREAD,
        contract="implement",
        terminal_status="failed",
        sdk_body=_clean_body("blocked"),
    )
    assert verdict == type(verdict)(False, "reflex_disabled")


def test_empty_body_does_not_fire() -> None:
    verdict = evaluate_reflex(
        thread_id=THREAD,
        contract="implement",
        terminal_status="completed",
        sdk_body="   ",
    )
    assert verdict.fire is False
    assert verdict.reason == "no_closeout_body"


@pytest.mark.parametrize(
    ("terminal_status", "body", "expected"),
    [
        ("failed", _clean_body(), "executor_failed"),
        ("completed", _clean_body("partial"), "weak_closeout_status"),
        ("completed", _clean_body("blocked"), "weak_closeout_status"),
        (
            "completed",
            "status: complete\nac_verdict: AC1=pass AC2=fail\n",
            "ac_verdict_miss",
        ),
        (
            "completed",
            "status: complete\nac_verdict: AC1=not_tested\n",
            "ac_verdict_miss",
        ),
        (
            "completed",
            "status: complete\nopen forks: should the module be split?\n",
            "executor_escalated",
        ),
    ],
)
def test_weak_closeout_shapes_fire(
    terminal_status: str, body: str, expected: str
) -> None:
    verdict = evaluate_reflex(
        thread_id=THREAD,
        contract="implement",
        terminal_status=terminal_status,
        sdk_body=body,
    )
    assert verdict.fire is True
    assert verdict.reason == expected


def test_sensitive_paths_fire_only_on_write_contracts() -> None:
    body = "status: complete\nac_verdict: AC1=pass\neffects: edited libs/model_id/wire.py\n"
    fired = evaluate_reflex(
        thread_id=THREAD,
        contract="implement",
        terminal_status="completed",
        sdk_body=body,
    )
    assert fired.fire is True
    assert fired.reason == "sensitive_paths_touched"

    quiet = evaluate_reflex(
        thread_id=f"{THREAD}-investigate",
        contract="investigate",
        terminal_status="completed",
        sdk_body=body,
    )
    assert quiet.fire is False


def test_sparse_directive_fires() -> None:
    verdict = evaluate_reflex(
        thread_id=THREAD,
        contract="implement",
        terminal_status="completed",
        sdk_body=_clean_body(),
        density="sparse",
    )
    assert verdict.fire is True
    assert verdict.reason == "sparse_directive_scope"


def test_periodic_sample_fires_on_nth_clean_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_AUTO_REFLEX_SAMPLE_EVERY", "3")
    reasons = [
        evaluate_reflex(
            thread_id=THREAD,
            contract="implement",
            terminal_status="completed",
            sdk_body=_clean_body(),
        ).reason
        for _ in range(3)
    ]
    assert reasons == ["no_trigger", "no_trigger", "periodic_sample"]


def test_budget_exhaustion_stops_firing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_AUTO_REFLEX_BUDGET", "2")
    for _ in range(2):
        assert evaluate_reflex(
            thread_id=THREAD,
            contract="implement",
            terminal_status="failed",
            sdk_body=_clean_body(),
        ).fire is True
        counters().note_spend(THREAD)

    blocked = evaluate_reflex(
        thread_id=THREAD,
        contract="implement",
        terminal_status="failed",
        sdk_body=_clean_body(),
    )
    assert blocked.fire is False
    assert blocked.reason == "budget_exhausted:2"


def test_budget_is_per_thread() -> None:
    counters().note_spend(THREAD)
    counters().note_spend(THREAD)
    counters().note_spend(THREAD)
    other = evaluate_reflex(
        thread_id="t-other",
        contract="implement",
        terminal_status="failed",
        sdk_body=_clean_body(),
    )
    assert other.fire is True


# --- packet, parse, inject --------------------------------------------------


def test_packet_carries_read_only_boundary_and_sentinels() -> None:
    packet = build_reflex_packet(
        directive_body="do the thing",
        closeout_body=_clean_body(),
        executor_model="cursor/composer-2.5",
        contract="implement",
        executor_dispatch_id="auto-abc123",
    )
    assert "READ ONLY" in packet
    assert "Do not edit, create, delete, stage, or commit any file." in packet
    assert SECOND_READ_BEGIN in packet and SECOND_READ_END in packet
    assert "auto-abc123" in packet
    assert "do the thing" in packet


def test_parse_extracts_answer_and_ignores_template_skeleton() -> None:
    body = (
        f"{SECOND_READ_BEGIN}\n1. EVIDENCE — …\n2. LIKELIEST ERROR — …\n"
        f"3. MISSING — …\n{SECOND_READ_END}\n"
        "some executor chatter\n"
        f"{SECOND_READ_BEGIN}\n1. EVIDENCE — AC2 cites no file.\n"
        f"2. LIKELIEST ERROR — the migration was never run.\n"
        f"3. MISSING — no rollback path.\n{SECOND_READ_END}\n"
    )
    answer = parse_second_read(body)
    assert answer is not None
    assert "AC2 cites no file" in answer
    assert "1. EVIDENCE — …" not in answer


def test_parse_returns_none_without_sentinels() -> None:
    assert parse_second_read("just prose, no sentinels") is None
    assert parse_second_read("") is None
    assert parse_second_read(None) is None


def test_parse_clamps_long_answers() -> None:
    long_answer = "x" * (MAX_SECOND_READ_CHARS * 2)
    parsed = parse_second_read(f"{SECOND_READ_BEGIN}\n{long_answer}\n{SECOND_READ_END}")
    assert parsed is not None
    assert "clamped at" in parsed
    assert len(parsed) < MAX_SECOND_READ_CHARS * 2


def test_reserved_status_words_are_scrubbed() -> None:
    scrubbed = scrub_reserved_status("I have verified and ratified this work.")
    assert "verified" not in scrubbed
    assert "ratified" not in scrubbed
    assert "assessed" in scrubbed


def test_injection_appends_without_disturbing_the_closeout() -> None:
    relay = _clean_body()
    out = inject_second_read_block(
        relay,
        text="1. EVIDENCE — thin.",
        model="cursor/claude-opus-5",
        reflex_dispatch_id="auto-reflex1",
        reason="ac_verdict_miss",
    )
    # The executor's own payload must survive verbatim, checkpoint line included.
    assert relay.strip() in out
    assert "checkpoint: committed abc123 paths=1" in out
    assert "## SECOND READ (advisory — not a ratification)" in out
    assert (
        "second_read(by=cursor/claude-opus-5, ref=auto-reflex1, trigger=ac_verdict_miss)"
        in out
    )
    assert "1. EVIDENCE — thin." in out


def test_injection_of_empty_text_is_a_noop() -> None:
    relay = _clean_body()
    assert inject_second_read_block(
        relay,
        text="   ",
        model="cursor/claude-opus-5",
        reflex_dispatch_id="auto-reflex1",
        reason="periodic_sample",
    ) == relay


# --- review findings: real §2 shapes, contamination, failure isolation --------


_BOLD_CLEAN = (
    "**status:** complete\n"
    "**ac_verdict:** PASS — all green\n"
    "**open forks:** none\n"
    "**effects:** none\n"
)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("**status:** partial\n**ac_verdict:** PASS\n", "weak_closeout_status"),
        ("| status | blocked |\n", "weak_closeout_status"),
        ("## status\ncomplete\n\n### ac_verdict\nAC1 — FAIL on auth\n", "ac_verdict_miss"),
        (
            "**status:** complete\n**ac_verdict:** AC2 not_tested\n",
            "ac_verdict_miss",
        ),
        (
            "**status:** complete\n**ac_verdict:** PASS\n"
            "**open forks:** should the module be split?\n",
            "executor_escalated",
        ),
        (
            "**status:** complete\n**ac_verdict:** PASS\n**open forks:** none\n"
            "| effects | libs/claude_bundles/catalog.py |\n",
            "sensitive_paths_touched",
        ),
    ],
)
def test_real_section2_shapes_fire(body: str, expected: str) -> None:
    """Executors author §2 as bold, heading, or table — all three must trigger.

    A predicate written only against ``field: value`` line starts is silently
    dead against the shapes the fleet actually emits.
    """
    verdict = evaluate_reflex(
        thread_id=THREAD,
        contract="implement",
        terminal_status="completed",
        sdk_body=body,
    )
    assert verdict.fire is True
    assert verdict.reason == expected


def test_prose_mention_of_sensitive_path_does_not_fire() -> None:
    """Citing ``libs/`` while explaining work is not touching it."""
    verdict = evaluate_reflex(
        thread_id=THREAD,
        contract="implement",
        terminal_status="completed",
        sdk_body=f"{_BOLD_CLEAN}I read libs/model_id/wire.py to orient before editing.\n",
    )
    assert verdict.fire is False


def test_passing_ac_mentioning_partial_does_not_fire() -> None:
    """``reason=partial coverage`` on a passing AC is not an AC miss."""
    verdict = evaluate_reflex(
        thread_id=THREAD,
        contract="implement",
        terminal_status="completed",
        sdk_body=(
            "**status:** complete\n"
            "**ac_verdict:** AC1=pass reason=partial coverage\n"
            "**open forks:** none\n**effects:** none\n"
        ),
    )
    assert verdict.fire is False


def test_injected_advisory_cannot_hijack_envelope_status_or_auth_gate() -> None:
    """The advisory is appended to the body every envelope scanner then reads.

    An advisory that quotes the executor must not be mistaken for the executor:
    a quoted ``status:`` or ``ac_verdict:`` would otherwise flip the relayed
    status or stamp an auth gate on a clean mission.
    """
    from services.git_integration_worker.cursor_auto.auth_gate_budget import (
        tag_gate_class_for_payload,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_status,
    )

    closeout = f"TYPE: CLOSEOUT\nstatus: complete\n\n{_BOLD_CLEAN}"
    advisory = (
        "Thin evidence. status: blocked is what this should have said.\n"
        "Your ac_verdict: AC1=fail at the sign in step was never reproduced.\n"
        "| status | blocked |\n"
        "### ac_verdict\nRe-run before trusting it."
    )
    merged = inject_second_read_block(
        closeout,
        text=advisory,
        model="cursor/claude-opus-5",
        reflex_dispatch_id="d-reflex",
        reason="ac_verdict_miss",
    )
    assert extract_status(advisory) == "blocked"  # the hazard is real
    assert extract_status(merged) == "complete"  # ...and neutralized
    assert tag_gate_class_for_payload(merged) is None
    assert "Re-run before trusting it." in merged


def test_second_read_swallows_exceptions_so_the_closeout_still_relays() -> None:
    """An advisory leg may never cost the caller the executor's real result."""
    import asyncio

    from services.git_integration_worker.cursor_auto import reflex_read
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    def _boom(**_kwargs: object) -> object:
        raise RuntimeError("ledger exploded")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(reflex_read, "evaluate_reflex", _boom)
    try:
        outcome = asyncio.run(
            reflex_read.maybe_run_second_read(
                AutoJob(
                    job_id="j1",
                    thread_id=THREAD,
                    turn_number=1,
                    subject="s",
                    body="b",
                    from_agent="a",
                    to_agent="cursor-auto",
                    desired_model="",
                    desired_effort="",
                    contract="implement",
                ),
                contract="implement",
                terminal_status="completed",
                sdk_body=_BOLD_CLEAN,
                executor_model="cursor/composer-2.5",
                executor_dispatch_id="d-exec",
            )
        )
    finally:
        monkey.undo()
    assert outcome is None
