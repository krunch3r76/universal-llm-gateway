"""Second-read reflex — knob composition, firing predicate, packet parse/inject.

These units are where a bug turns into unmetered premium spend (a predicate that
fires on every closeout) or into a corrupted relay (an injection that eats the
executor's own §2 payload), so they are covered directly rather than through the
async dispatch path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.git_integration_worker.cursor_auto.knob_compose import compose_model_knobs
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
    reflex_sample_every,
)
from services.git_integration_worker.cursor_auto.reflex_read import (
    _DEFAULT_EFFORT,
    _DEFAULT_MODEL,
    reflex_effort,
    reflex_knobs,
    reflex_model,
)

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


def test_default_reflex_model_is_luna_not_opus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURSOR_AUTO_REFLEX_MODEL", raising=False)
    assert _DEFAULT_MODEL == "cursor/gpt-5.6-luna"
    assert reflex_model() == "cursor/gpt-5.6-luna"


def test_default_reflex_effort_is_max(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unlike a primary DIRECTIVE, this default is not subject to
    # dispatch_bounds.clamp_effort_to_model_card (handler.py-only) —
    # max is expected to reach the model unclamped, bounded by the poll
    # timeout rather than the effort knob (agent-bus:7372, 2026-08-16).
    monkeypatch.delenv("CURSOR_AUTO_REFLEX_EFFORT", raising=False)
    assert _DEFAULT_EFFORT == "max"
    assert reflex_effort() == "max"


def test_reflex_model_env_override_still_honors_opus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CURSOR_AUTO_REFLEX_MODEL", "cursor/claude-opus-5")
    assert reflex_model() == "cursor/claude-opus-5"


# --- knob composition -------------------------------------------------------


def test_effort_merges_onto_opus_and_preserves_base_knobs() -> None:
    knobs = compose_model_knobs(
        {
            "resolved_model_id": "cursor/claude-opus-5",
            "model_knobs": {"thinking": "true"},
        },
        {"resolved_effort": "low"},
    )
    assert knobs == {"thinking": "true", "effort": "low"}


def test_effort_within_accepted_range_passes_through() -> None:
    # grok's own accepted range includes xhigh (cursor_capabilities.py) — no
    # degradation needed for a value the model already accepts verbatim.
    # Auto defaults fast=false when the knob is absent (catalog default is true).
    knobs = compose_model_knobs(
        {"resolved_model_id": "cursor/grok-4.6"}, {"resolved_effort": "xhigh"}
    )
    assert knobs == {"effort": "xhigh", "fast": "false"}


def test_effort_clamps_down_to_model_ceiling() -> None:
    # grok tops out at xhigh (only opus's ladder reaches max); an
    # out-of-range value must degrade to the nearest accepted rung below it
    # rather than drop the knob entirely, which would silently hand the
    # bridge a model default that could be far above what was asked for.
    knobs = compose_model_knobs(
        {"resolved_model_id": "cursor/grok-4.6"}, {"resolved_effort": "max"}
    )
    assert knobs == {"effort": "xhigh", "fast": "false"}


def test_grok_auto_defaults_fast_false_even_without_effort() -> None:
    knobs = compose_model_knobs(
        {"resolved_model_id": "cursor/grok-4.6"}, {"resolved_effort": ""}
    )
    assert knobs == {"fast": "false"}


def test_grok_explicit_fast_true_is_preserved() -> None:
    """Default is fill-if-absent, not a pin — an explicit fast rides through."""
    knobs = compose_model_knobs(
        {
            "resolved_model_id": "cursor/grok-4.6",
            "model_knobs": {"fast": "true"},
        },
        {"resolved_effort": "high"},
    )
    assert knobs == {"effort": "high", "fast": "true"}


def test_model_without_effort_knob_gets_none() -> None:
    knobs = compose_model_knobs(
        {"resolved_model_id": "cursor/composer-2.5"}, {"resolved_effort": "high"}
    )
    assert knobs == {}


def test_unknown_model_does_not_raise() -> None:
    assert (
        compose_model_knobs(
            {"resolved_model_id": "cursor/not-a-model"}, {"resolved_effort": "low"}
        )
        == {}
    )
    assert compose_model_knobs({}, {"resolved_effort": "low"}) == {}


def test_reflex_knobs_luna_uses_gpt_context_not_anthropic_300k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default reflex model is Luna; 300k is Anthropic-only and must not ship."""
    monkeypatch.delenv("CURSOR_AUTO_REFLEX_EFFORT", raising=False)
    knobs = reflex_knobs("cursor/gpt-5.6-luna")
    assert knobs.get("context") == "272k"
    assert "300k" not in knobs.values()


def test_reflex_knobs_opus_keeps_lean_300k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURSOR_AUTO_REFLEX_EFFORT", raising=False)
    knobs = reflex_knobs("cursor/claude-opus-5")
    assert knobs.get("context") == "300k"


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


@pytest.mark.parametrize(
    "contract", ["answer", "confer", "execute", "propagate", "seed"]
)
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


def test_periodic_sample_does_not_fire_even_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert reasons == ["no_trigger", "no_trigger", "no_trigger"]
    assert reflex_sample_every() == 0


def test_budget_exhaustion_stops_firing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_AUTO_REFLEX_BUDGET", "2")
    for _ in range(2):
        assert (
            evaluate_reflex(
                thread_id=THREAD,
                contract="implement",
                terminal_status="failed",
                sdk_body=_clean_body(),
            ).fire
            is True
        )
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
    assert (
        inject_second_read_block(
            relay,
            text="   ",
            model="cursor/claude-opus-5",
            reflex_dispatch_id="auto-reflex1",
            reason="periodic_sample",
        )
        == relay
    )


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
        (
            "## status\ncomplete\n\n### ac_verdict\nAC1 — FAIL on auth\n",
            "ac_verdict_miss",
        ),
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


# --- identity: bind_job, sidecar parse, failed-submit rebind, read_only settle -


@pytest.fixture
def isolated_auto_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from services.git_integration_worker.cursor_auto.job_ledger import AutoJobLedger
    from services.git_integration_worker.cursor_auto.queue import reset_queue_for_tests

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    AutoJobLedger.reset_for_tests()
    reset_queue_for_tests(durable=True)
    yield
    AutoJobLedger.reset_for_tests()


def _enqueue_job(thread_id: str = THREAD, contract: str = "implement"):
    from services.git_integration_worker.cursor_auto.queue import get_queue

    return get_queue().enqueue(
        thread_id=thread_id,
        turn_number=1,
        subject="reflex identity",
        body="TYPE: DIRECTIVE\ncontract: implement\n",
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="medium",
        contract=contract,
    )


def _patch_nested_http(monkeypatch: pytest.MonkeyPatch, *, status: int) -> None:
    from unittest.mock import AsyncMock, MagicMock

    mock_client = AsyncMock()
    if status >= 400:
        mock_resp = MagicMock()
        mock_resp.status_code = status
        mock_resp.content = b'{"detail":"rejected"}'
        mock_resp.json.return_value = {"detail": "rejected"}
        mock_client.post = AsyncMock(return_value=mock_resp)
    else:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"admitted": true}'
        mock_resp.json.return_value = {"admitted": True}
        mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    class _FakeCM:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> object:
            return mock_client

        async def __aexit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_sdk.httpx.AsyncClient",
        _FakeCM,
    )


def test_reflex_bind_does_not_clobber_executor_dispatch(
    isolated_auto_ledger: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from services.git_integration_worker.cursor_auto.job_ledger import get_ledger
    from services.git_integration_worker.cursor_auto.nested_sdk import (
        submit_nested_dispatch,
    )

    job = _enqueue_job()
    _patch_nested_http(monkeypatch, status=200)
    first = asyncio.run(
        submit_nested_dispatch(
            job,
            model_id="cursor/composer-2.5",
            handoff_contract="implement",
            message="go",
        )
    )
    executor_id = str(first["dispatch_id"])
    assert first["ok"] is True
    assert get_ledger().read_relay_state(job.job_id)["dispatch_id"] == executor_id

    second = asyncio.run(
        submit_nested_dispatch(
            job,
            model_id="cursor/gpt-5.6-luna",
            handoff_contract="light-bounded",
            message="read",
            read_only=True,
            bind_job=False,
        )
    )
    assert second["ok"] is True
    assert second["dispatch_id"] != executor_id
    state = get_ledger().read_relay_state(job.job_id)
    assert state["dispatch_id"] == executor_id


def test_failed_submit_can_rebind_to_live_executor(
    isolated_auto_ledger: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead first-submit id must not pin the job; LWW rebind is the belt."""
    import asyncio

    from services.git_integration_worker.cursor_auto.job_ledger import (
        RELAY_PHASE_DISPATCHED,
        RELAY_PHASE_NONE,
        get_ledger,
    )
    from services.git_integration_worker.cursor_auto.nested_sdk import (
        submit_nested_dispatch,
    )

    job = _enqueue_job()
    _patch_nested_http(monkeypatch, status=503)
    failed = asyncio.run(
        submit_nested_dispatch(
            job,
            model_id="cursor/composer-2.5",
            handoff_contract="implement",
            message="go",
        )
    )
    assert failed["ok"] is False
    dead_id = str(failed["dispatch_id"])
    state = get_ledger().read_relay_state(job.job_id)
    assert state["dispatch_id"] == dead_id
    assert state["relay_phase"] == RELAY_PHASE_NONE

    _patch_nested_http(monkeypatch, status=200)
    live = asyncio.run(
        submit_nested_dispatch(
            job,
            model_id="cursor/composer-2.5",
            handoff_contract="implement",
            message="retry",
        )
    )
    assert live["ok"] is True
    live_id = str(live["dispatch_id"])
    assert live_id != dead_id
    state = get_ledger().read_relay_state(job.job_id)
    assert state["dispatch_id"] == live_id
    assert state["relay_phase"] == RELAY_PHASE_DISPATCHED


def test_failed_reflex_submit_does_not_reset_executor_phase(
    isolated_auto_ledger: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from services.git_integration_worker.cursor_auto.job_ledger import (
        RELAY_PHASE_DISPATCHED,
        get_ledger,
    )
    from services.git_integration_worker.cursor_auto.nested_sdk import (
        submit_nested_dispatch,
    )

    job = _enqueue_job()
    _patch_nested_http(monkeypatch, status=200)
    first = asyncio.run(
        submit_nested_dispatch(
            job,
            model_id="cursor/composer-2.5",
            handoff_contract="implement",
            message="go",
        )
    )
    executor_id = str(first["dispatch_id"])
    _patch_nested_http(monkeypatch, status=503)
    failed = asyncio.run(
        submit_nested_dispatch(
            job,
            model_id="cursor/gpt-5.6-luna",
            handoff_contract="light-bounded",
            message="read",
            read_only=True,
            bind_job=False,
        )
    )
    assert failed["ok"] is False
    state = get_ledger().read_relay_state(job.job_id)
    assert state["dispatch_id"] == executor_id
    assert state["relay_phase"] == RELAY_PHASE_DISPATCHED


def test_reflex_parses_repo_sidecar_not_bus_envelope(
    isolated_auto_ledger: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from services.git_integration_worker.cursor_auto.queue import AutoJob
    from services.git_integration_worker.cursor_auto.reflex_packet import (
        SECOND_READ_BEGIN,
        SECOND_READ_END,
    )
    from services.git_integration_worker.cursor_auto.reflex_read import (
        _run_reflex_dispatch,
    )

    sidecar = (
        f"{SECOND_READ_BEGIN}\n"
        "1. EVIDENCE — sidecar hit.\n"
        "2. LIKELIEST ERROR — bus envelope parse.\n"
        "3. MISSING — inject into Auto CLOSEOUT.\n"
        f"{SECOND_READ_END}\n"
    )
    bus_json = '{"status":"complete","text":"no sentinels in the envelope"}'

    async def _ok_submit(*_a: object, **kwargs: object) -> dict[str, object]:
        assert kwargs.get("bind_job") is False
        assert kwargs.get("read_only") is True
        return {"ok": True, "dispatch_id": "auto-reflex-sidecar"}

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_sdk.submit_nested_dispatch",
        _ok_submit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_sdk.fetch_sdk_closeout_body",
        AsyncMock(return_value=bus_json),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.closeout_relay.read_repo_closeout_sidecar",
        lambda dispatch_id, **_k: (
            sidecar if dispatch_id == "auto-reflex-sidecar" else None
        ),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.reflex_read._poll_reflex_terminal",
        AsyncMock(return_value={"terminal": True, "status": "completed", "row": {}}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.reflex_read.maybe_emit_premium_bind",
        lambda **_k: None,
    )
    emitted: list[str] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.reflex_read._emit_outcome",
        lambda *_a, **_k: emitted.append(str(_a[-1] if _a else _k.get("outcome"))),
    )

    job = AutoJob(
        job_id="j-sidecar",
        thread_id=THREAD,
        turn_number=1,
        subject="s",
        body="do the thing",
        from_agent="a",
        to_agent="cursor-auto",
        desired_model="",
        desired_effort="",
        contract="implement",
    )
    outcome = asyncio.run(
        _run_reflex_dispatch(
            job,
            contract="implement",
            model_id="cursor/gpt-5.6-luna",
            knobs={},
            sdk_body=_clean_body(),
            executor_model="cursor/composer-2.5",
            executor_dispatch_id="d-exec",
            bus=None,
            superseded=None,
            reason="sparse_directive_scope",
        )
    )
    assert outcome is not None
    assert "sidecar hit" in outcome.text
    assert "no sentinels in the envelope" not in outcome.text
    assert emitted[-1] == "delivered"


def test_read_only_finalize_skips_lane_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
    from services.git_integration_worker.cursor_sdk_closeout.closeout_records import (
        SdkRunOutcome,
    )
    from services.git_integration_worker.cursor_sdk_closeout.delivery_assembly import (
        receipt_finalization as rf,
    )

    called: list[dict[str, object]] = []
    monkeypatch.setattr(
        rf,
        "settle_lane_branch",
        lambda **kwargs: called.append(kwargs),
    )
    monkeypatch.setattr(
        rf, "persist_structured_closeout_full_to_repo_sidecar", lambda **_k: None
    )
    monkeypatch.setattr(rf, "render_usage_sidecar_section", lambda **_k: "")
    sidecar = tmp_path / "sidecar.md"
    sidecar.write_text("closeout\n", encoding="utf-8")
    body = json.dumps({"status": "complete"})
    outcome = SdkRunOutcome(
        body="", status="complete", duration_ms=1, tool_call_count=0
    )
    cs = ChangeSet(created=(), modified=("x.py",), deleted=())
    kwargs = dict(
        source_repo=tmp_path,
        lane_b_branch="cursor-sdk/lane-9470",
        thread_id="9470",
        dispatch_id="auto-reflex1",
        text="land_disposition: landed\n",
        capture_commits_ahead=1,
        capture_landed=False,
        capture_head_sha="abc",
        repo_change_set=cs,
        outcome=outcome,
        resolved_model="cursor/gpt-5.6-luna",
        sidecar_appendix=[],
        sidecar_path=sidecar,
        result_bytes=1,
        body=body,
        sidecar_ref="ref",
        execution_id="exec",
        finalize_oversize=False,
        post_closeout_sidecar_fn=None,
    )
    rf.finalize_closeout_receipt(**kwargs, read_only=True)
    assert called == []
    rf.finalize_closeout_receipt(**kwargs, read_only=False)
    assert len(called) == 1
    assert called[0]["branch_name"] == "cursor-sdk/lane-9470"
    assert called[0]["dispatch_id"] == "auto-reflex1"
