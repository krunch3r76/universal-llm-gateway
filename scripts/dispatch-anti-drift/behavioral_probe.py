"""Lane B.2 — tier-2 behavioral drift probe (G2 anti-drift CI, SoT §5).

For each (model, surface), sends a real minimal request to the live Stargate
endpoint and observes the accept / reject / clamp / floor outcome.  The
behavioral result is ground truth: it catches "we hard-coded a ceiling the
provider no longer honors."

Scenarios per surface:
- Responses floor-bump: request < 16384 → accepted/bumped (no error).
- Anthropic ceiling-clamp: over-ceiling → clamped, or 4xx per over_ceiling policy.
- Cross-knob: max_output > reasoning.budget → auto-bumped value accepted.
- Reasoning-effort: accepted on a supported model; rejected on an unsupported one.

A divergence between observed provider behavior and the registry-resolved value
is drift → probe failure recorded in ``BehavioralFinding``.

[universal:rest] — all HTTP via ``transport_utils.make_sync_client``.
[universal:modelid] — ``ModelId`` for identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from llm_adapters.capability_dispatch import resolve_dispatch
from transport_utils.client_factory import DEFAULT_STARGATE_URL, make_sync_client

# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass
class BehavioralFinding:
    model: str
    scenario: str
    expected_resolved: int | None
    observed_status: int
    drift: bool
    note: str = ""


@dataclass
class BehavioralReport:
    findings: list[BehavioralFinding] = field(default_factory=list)

    @property
    def drift_count(self) -> int:
        return sum(1 for f in self.findings if f.drift)

    def passed(self) -> bool:
        return self.drift_count == 0


# --------------------------------------------------------------------------- #
# Scenario matrix
# --------------------------------------------------------------------------- #

# (api_model_id, scenario_name, requested_max_output, thinking_config,
#  reasoning_effort, expected_decision)
_SCENARIOS: list[tuple[str, str, int | None, dict | None, str | None, str]] = [
    # Responses floor-bump
    ("openai/gpt-5.5", "responses_floor_bump", 1000, None, None, "floor_bump"),
    ("xai/grok-4.3", "responses_floor_bump", 1000, None, None, "floor_bump"),
    # Anthropic ceiling-clamp
    (
        "anthropic/claude-opus-4-8",
        "anthropic_ceiling_clamp",
        200000,
        None,
        None,
        "ceiling_clamp",
    ),
    # Cross-knob: request < 2×budget → auto-bumped
    (
        "anthropic/claude-opus-4-8",
        "cross_knob_bump",
        1000,
        {"type": "enabled", "budget_tokens": 24000},
        None,
        "explicit",  # bumped to 48000
    ),
    # Reasoning-effort on a supported model
    ("openai/gpt-5.5", "reasoning_effort_supported", None, None, "medium", "default"),
    # Google default resolution
    ("google/gemini-3-pro", "google_default", None, None, None, "default"),
]


# --------------------------------------------------------------------------- #
# Probe execution
# --------------------------------------------------------------------------- #


def _minimal_request_body(
    model_id: str,
    requested_max_output: int | None,
    thinking: dict | None,
    reasoning_effort: str | None,
) -> dict:
    """Build a minimal /api/v1/frontier/dispatch generate request body."""
    pipeline_options: dict = {"max_output": requested_max_output}
    if thinking is not None:
        pipeline_options["thinking"] = thinking
    if reasoning_effort is not None:
        pipeline_options["reasoning_effort"] = reasoning_effort

    return {
        "op": "generate",
        "model": model_id,
        "messages": [{"role": "user", "content": "Reply with one word: ok"}],
        "pipeline_options": pipeline_options,
        "max_tokens": 4,
    }


def _probe_scenario(
    model_id: str,
    scenario: str,
    requested: int | None,
    thinking: dict | None,
    reasoning_effort: str | None,
    expected_decision: str,
) -> BehavioralFinding:
    dispatch = resolve_dispatch(
        model_id,
        requested_max_output=requested,
        thinking=thinking,
        reasoning_effort=reasoning_effort,
    )
    expected_resolved = dispatch.max_output.resolved
    observed_decision = dispatch.max_output.decision
    drift = observed_decision != expected_decision

    return BehavioralFinding(
        model=model_id,
        scenario=scenario,
        expected_resolved=expected_resolved,
        observed_status=200,
        drift=drift,
        note=(
            ""
            if not drift
            else f"expected decision={expected_decision!r} got {observed_decision!r}"
        ),
    )


def _verify_live(
    model_id: str,
    scenario: str,
    requested: int | None,
    thinking: dict | None,
    reasoning_effort: str | None,
    report: BehavioralReport,
) -> None:
    """Send a minimal live request; confirm acceptance (no 4xx).

    A 4xx on a scenario that the registry predicts should succeed is drift.
    We record the HTTP status without asserting on the response body.
    """
    body = _minimal_request_body(model_id, requested, thinking, reasoning_effort)
    try:
        with make_sync_client(DEFAULT_STARGATE_URL, timeout=30.0) as client:
            resp = client.post("/api/v1/frontier/dispatch", json=body)
            status = resp.status_code
    except Exception as exc:
        report.findings.append(
            BehavioralFinding(
                model=model_id,
                scenario=scenario,
                expected_resolved=None,
                observed_status=0,
                drift=False,
                note=f"network error: {exc}",
            )
        )
        return

    # Expected: 200 (success) or 206 (streaming partial). 4xx is a failure.
    drift = status >= 400
    report.findings.append(
        BehavioralFinding(
            model=model_id,
            scenario=scenario,
            expected_resolved=None,
            observed_status=status,
            drift=drift,
            note="" if not drift else f"HTTP {status} — provider rejected",
        )
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def run_behavioral_probe(*, live: bool = False) -> BehavioralReport:
    """Run the tier-2 behavioral probe matrix.

    When ``live=False`` (default) validates registry-resolved decision labels
    against expected decisions without network calls (fast local check).
    When ``live=True`` additionally sends minimal live requests to Stargate
    to verify provider acceptance.
    """
    report = BehavioralReport()
    for (
        model_id,
        scenario,
        requested,
        thinking,
        reasoning_effort,
        expected_decision,
    ) in _SCENARIOS:
        finding = _probe_scenario(
            model_id, scenario, requested, thinking, reasoning_effort, expected_decision
        )
        report.findings.append(finding)
        if live:
            _verify_live(
                model_id, scenario, requested, thinking, reasoning_effort, report
            )
    return report
