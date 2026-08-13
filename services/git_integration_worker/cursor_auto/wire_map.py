"""Pure wire-map for agent_bus.request desired_model / effort / contract.

Inline projection of dense-spec Impl §4 — no I/O. Unit-tested.
"""

from __future__ import annotations

from typing import Any, Literal

from cursor_capabilities import (
    canonical_cursor_bare_id,
    effort_knob_name,
    supported_knobs,
)
from effort_vocabulary import WIRE_LADDER, normalize_effort

Contract = Literal[
    "answer", "confer", "investigate", "implement", "verify", "execute", "propagate", "seed"
]
Disposition = Literal[
    "answered",
    "conferred",
    "dispatched-and-relayed",
    "needs-attended",
    "declined",
    "executed",
    "propagated",
]

_MODEL_TABLE: dict[str, str] = {
    "composer-2.5": "cursor/composer-2.5",
    "grok-4.6": "cursor/grok-4.6",
    "opus-5": "cursor/claude-opus-5",
}
BINDABLE_WIRE_IDS: tuple[str, ...] = tuple(sorted(_MODEL_TABLE))
_BINDABLE_MODEL_IDS: frozenset[str] = frozenset(_MODEL_TABLE.values())


def format_bindable_models() -> str:
    """Human-readable bindable set for operator-facing refusal text."""
    wire = ", ".join(BINDABLE_WIRE_IDS)
    prefixed = ", ".join(sorted(_BINDABLE_MODEL_IDS))
    return f"{wire} (or prefixed: {prefixed})"


def format_bindable_efforts() -> str:
    """Human-readable bindable effort ladder for operator-facing refusal text."""
    return ", ".join(BINDABLE_EFFORT_VALUES)


def format_bindable_escalations() -> str:
    """Human-readable bindable CDP escalation values for refusal text."""
    return ", ".join(BINDABLE_CDP_ESCALATIONS)


def format_cdp_escalation_hint() -> str:
    """Human-readable CDP reachability hint for model-pin refusal text (S1-f)."""
    values = ", ".join(BINDABLE_CDP_ESCALATIONS)
    return (
        f"escalation= ({values}) on agent_bus.request, or "
        f"team_dispatch(model=cdp/…) from a code seat"
    )


def coerce_cdp_desired_model_alias(raw: str | None) -> str | None:
    """Return canonical escalation if ``raw`` is a CDP-on-desired_model alias."""
    key = (raw or "").strip().lower()
    if not key:
        return None
    return _CDP_DESIRED_MODEL_ALIASES.get(key)


def coalesce_cdp_desired_model_into_escalation(
    desired_model: str | None,
    escalation: str | None,
) -> tuple[str, str | None, dict[str, Any]]:
    """Move ``desired_model=cdp/*`` onto ``escalation`` so admits succeed.

    Fresh operator-proxy seats commonly pin Fable/Opus on ``desired_model``
    (cursor-sdk vocabulary). That used to ``model_pin_refused``. When the value
    is a known CDP escalation alias and ``escalation`` is empty (or already the
    same), rewrite ``desired_model→auto`` and set ``escalation``. Conflicting
    pins (cdp/fable on model + cdp/opus-5 on escalation) are left unchanged for
    the normal refusal path.
    """
    requested_model = (desired_model or "auto").strip() or "auto"
    esc_raw = (escalation or "").strip()
    esc = esc_raw.lower() if esc_raw else None
    alias = coerce_cdp_desired_model_alias(requested_model)
    if alias is None:
        return requested_model, (esc_raw or None), {"coalesced": False}
    if esc and esc != alias:
        return (
            requested_model,
            esc_raw or None,
            {
                "coalesced": False,
                "conflict": True,
                "desired_model_as_escalation": alias,
                "escalation": esc,
            },
        )
    return (
        "auto",
        alias,
        {
            "coalesced": True,
            "from_desired_model": requested_model.strip().lower(),
            "to_escalation": alias,
            "notes": (
                f"coalesced desired_model={requested_model!r} → "
                f"escalation={alias!r} (desired_model=auto)"
            ),
        },
    )


def _model_pin_refusal(requested: str) -> str:
    """Refusal line for unknown ``desired_model`` including CDP route naming."""
    return (
        f"unknown desired_model={requested!r}; bindable: {format_bindable_models()}. "
        f"For CDP/Fable consult use {format_cdp_escalation_hint()}"
    )


def _lookup_explicit_model(raw: str) -> str | None:
    """Map bare or ``cursor/``-prefixed hint → canonical ``model_id``."""
    bare = _MODEL_TABLE.get(raw)
    if bare is not None:
        return bare
    if raw.startswith("cursor/") and raw in _BINDABLE_MODEL_IDS:
        return raw
    return None
# Canonical wire vocabulary — owned by libs/effort_vocabulary (aliases too).
_EFFORT_LADDER: tuple[str, ...] = WIRE_LADDER
_EFFORT_VALUES = frozenset(_EFFORT_LADDER)
BINDABLE_EFFORT_VALUES: tuple[str, ...] = _EFFORT_LADDER
BINDABLE_CDP_ESCALATIONS: tuple[str, ...] = ("cdp/opus-5", "cdp/fable")
# Life seats often put CDP models on desired_model by mistake; map → escalation.
_CDP_DESIRED_MODEL_ALIASES: dict[str, str] = {
    "cdp/opus-5": "cdp/opus-5",
    "cdp/opus": "cdp/opus-5",
    "cdp/opus5": "cdp/opus-5",
    "cdp/fable": "cdp/fable",
    "cdp/fable-5": "cdp/fable",
    "cdp/fable5": "cdp/fable",
}
_CONTRACTS = frozenset(
    {"answer", "confer", "investigate", "implement", "verify", "execute", "propagate", "seed"}
)


def resolve_desired_model(
    desired_model: str | None,
    *,
    contract: str = "answer",
) -> dict[str, Any]:
    """Map request ``desired_model`` hint → resolved ``model_id`` + notes.

    ``auto`` (default) picks by contract: answer→grok, investigate→grok,
    implement→composer, verify→composer. Explicit hints are honored and reported.
    Opus is never the auto default (lean-context implement ladder = composer).
    """
    raw = (desired_model or "auto").strip().lower() or "auto"
    if raw == "auto":
        by_contract = {
            "answer": "cursor/grok-4.6",
            "confer": "cursor/grok-4.6",
            "investigate": "cursor/grok-4.6",
            "implement": "cursor/composer-2.5",
            "verify": "cursor/composer-2.5",
            "seed": "cursor/grok-4.6",
        }
        model_id = by_contract.get(contract, "cursor/composer-2.5")
        return {
            "requested": "auto",
            "resolved_model_id": model_id,
            "honored": True,
            "notes": f"auto chose {model_id} for contract={contract}",
        }
    model_id = _lookup_explicit_model(raw)
    if model_id is None:
        return {
            "requested": raw,
            "resolved_model_id": None,
            "honored": False,
            "rejected": True,
            "bindable": BINDABLE_WIRE_IDS,
            "notes": (
                f"unknown desired_model={raw!r}; bindable: {format_bindable_models()}. "
                f"For CDP/Fable consult use {format_cdp_escalation_hint()}"
            ),
        }
    knobs: dict[str, str] = {}
    if model_id == "cursor/claude-opus-5":
        knobs = {"thinking": "true"}
    return {
        "requested": raw,
        "resolved_model_id": model_id,
        "honored": True,
        "model_knobs": knobs,
        "notes": "honored explicit desired_model",
    }


def assess_model_pin(
    wire_desired_model: str | None,
    *,
    contract: str,
    body: str,
) -> tuple[dict[str, Any], str | None]:
    """Resolve wire model pin and return ``(model, block_reason)``.

    ``block_reason`` is set when admit must refuse: unknown wire pin, or a body-level
    ``desired_model:`` line (wire-only contract — body form is not honored).
    """
    from services.git_integration_worker.cursor_auto.directive import (
        body_desired_model,
    )

    body_pin = body_desired_model(body)
    if body_pin is not None:
        model = resolve_desired_model(wire_desired_model, contract=contract)
        return (
            model,
            (
                f"body desired_model:{body_pin!r} ignored — model pin is wire-only; "
                f"set desired_model on agent_bus.request "
                f"(bindable: {format_bindable_models()})"
            ),
        )
    model = resolve_desired_model(wire_desired_model, contract=contract)
    if model.get("rejected"):
        return (
            model,
            _model_pin_refusal(str(model["requested"])),
        )
    return model, None


def resolve_escalation(escalation: str | None) -> dict[str, Any]:
    """Map wire ``escalation`` hint → resolved CDP model or absent.

    Values ``cdp/opus-5`` and ``cdp/fable`` honor; absent/empty ⇒ no CDP leg;
    unknown ⇒ ``rejected`` for admit refusal.
    """
    raw = (escalation or "").strip().lower()
    if not raw:
        return {
            "requested": "",
            "resolved_escalation": None,
            "honored": False,
            "notes": "no escalation requested",
        }
    if raw in BINDABLE_CDP_ESCALATIONS:
        return {
            "requested": raw,
            "resolved_escalation": raw,
            "honored": True,
            "notes": "honored explicit escalation",
        }
    return {
        "requested": raw,
        "resolved_escalation": None,
        "honored": False,
        "rejected": True,
        "bindable": BINDABLE_CDP_ESCALATIONS,
        "notes": (
            f"unknown escalation={raw!r}; bindable: {format_bindable_escalations()}"
        ),
    }


def assess_escalation_pin(
    wire_escalation: str | None,
    *,
    body: str,
) -> tuple[dict[str, Any], str | None]:
    """Resolve wire escalation pin and return ``(escalation, block_reason)``.

    ``block_reason`` is set when admit must refuse: unknown wire pin, or a
    body-level ``escalation:`` line (wire-only contract — body form is not honored).
    """
    from services.git_integration_worker.cursor_auto.directive import body_escalation

    body_pin = body_escalation(body)
    if body_pin is not None:
        esc = resolve_escalation(wire_escalation)
        return (
            esc,
            (
                f"body escalation:{body_pin!r} ignored — escalation pin is wire-only; "
                f"set escalation on agent_bus.request "
                f"(bindable: {format_bindable_escalations()})"
            ),
        )
    esc = resolve_escalation(wire_escalation)
    if esc.get("rejected"):
        return (
            esc,
            (
                f"unknown escalation={esc['requested']!r}; bindable: "
                f"{format_bindable_escalations()}"
            ),
        )
    return esc, None


def assess_effort_pin(
    wire_desired_effort: str | None,
    *,
    body: str,
) -> tuple[dict[str, Any], str | None]:
    """Resolve wire effort pin and return ``(effort, block_reason)``.

    ``block_reason`` is set when admit must refuse: a body-level effort line at an
    authoring position (line-start ``effort:``, ``reasoning_effort:``, or line-start
    ``model_knobs`` effort) — body form is not honored; use ``desired_effort`` on
    ``agent_bus.request``.
    """
    from services.git_integration_worker.cursor_auto.directive import body_effort_pin

    body_pin = body_effort_pin(body)
    if body_pin is not None:
        effort = resolve_desired_effort(wire_desired_effort)
        return (
            effort,
            (
                f"body effort:{body_pin!r} ignored — effort pin is wire-only; "
                f"set desired_effort on agent_bus.request "
                f"(bindable: {format_bindable_efforts()})"
            ),
        )
    effort = resolve_desired_effort(wire_desired_effort)
    return effort, None


def admit_model_override_rule_line(model: dict[str, Any]) -> str | None:
    """Surface resolve-path ``notes`` when requested model id ≠ resolved id.

    Reads ``model["notes"]`` authored by ``resolve_desired_model`` — never
    reconstructs rule text at print time (roadmap item 6 / D1).
    """
    requested = str(model.get("requested") or "").strip()
    resolved = str(model.get("resolved_model_id") or "").strip()
    if not requested or not resolved:
        return None
    if requested.casefold() == resolved.casefold():
        return None
    notes = model.get("notes")
    if not notes:
        return None
    return f"model_override_rule: {notes}"


def admit_effort_override_rule_line(effort: dict[str, Any]) -> str | None:
    """Surface resolve-path ``notes`` when requested effort ≠ resolved effort.

    Reads ``effort["notes"]`` authored by ``resolve_desired_effort`` — never
    reconstructs rule text at print time (symmetric to ``admit_model_override_rule_line``).
    """
    requested = str(effort.get("requested") or "").strip()
    resolved = str(effort.get("resolved_effort") or "").strip()
    if not requested or not resolved:
        return None
    if requested.casefold() == resolved.casefold():
        return None
    notes = effort.get("notes")
    if not notes:
        return None
    return f"effort_override_rule: {notes}"


def admit_model_pin_flags(
    model: dict[str, Any],
    effort: dict[str, Any],
) -> tuple[str, ...]:
    """Operator-visible admit flags when a pin was not fully honored."""
    flags: list[str] = []
    if not model.get("honored"):
        flags.append(f"model_pin_dropped: {model.get('notes', 'unhonored')}")
    if effort.get("clamped"):
        flags.append(f"effort_clamped: {effort.get('notes', 'clamped')}")
    return tuple(flags)


def resolve_desired_effort(desired_effort: str | None) -> dict[str, Any]:
    """Normalize + clamp ``desired_effort`` to canonical wire values.

    Canonical set: low|medium|high|xhigh|max. Surface aliases (``extra``,
    ``extra-high``, ``Extra High``) normalize to ``xhigh`` via effort_vocabulary.
    """
    requested = (desired_effort or "medium").strip().lower() or "medium"
    normalized = normalize_effort(requested)
    if normalized is not None and normalized in _EFFORT_VALUES:
        notes = "honored"
        if normalized != requested.replace(" ", "-"):
            notes = f"normalized {requested!r}→{normalized}"
        return {
            "requested": requested,
            "resolved_effort": normalized,
            "clamped": False,
            "notes": notes,
        }
    return {
        "requested": requested,
        "resolved_effort": "medium",
        "clamped": True,
        "notes": f"unknown desired_effort={requested!r}; clamped to medium",
    }


def resolve_contract_disposition(contract: str | None) -> dict[str, Any]:
    """Map request.contract → Auto disposition guidance (pure)."""
    raw = (contract or "answer").strip().lower() or "answer"
    if raw not in _CONTRACTS:
        return {
            "requested": raw,
            "contract": "answer",
            "disposition_hint": "answered",
            "notes": f"unknown contract={raw!r}; treated as answer",
        }
    hints: dict[str, Disposition] = {
        "answer": "answered",
        "confer": "conferred",
        "investigate": "dispatched-and-relayed",
        "implement": "dispatched-and-relayed",
        "verify": "dispatched-and-relayed",
        "execute": "executed",
        "propagate": "propagated",
        "seed": "dispatched-and-relayed",
    }
    return {
        "requested": raw,
        "contract": raw,
        "disposition_hint": hints[raw],
        "notes": "ok",
    }


def _clamp_effort_to_accepted(requested: str, accepted: tuple[str, ...]) -> str | None:
    """Pick the highest accepted rung at or below *requested* on the effort ladder.

    Descriptors disagree on ceiling (grok tops at ``high``, opus at ``max``), so a
    verbatim mismatch must degrade rather than drop the knob — dropping silently
    hands the bridge a model default that can be far above what was asked for.
    """
    if requested in accepted:
        return requested
    ladder = _EFFORT_LADDER
    if requested not in ladder:
        return None
    in_ladder = [value for value in ladder if value in accepted]
    if not in_ladder:
        return None
    below = [value for value in in_ladder if ladder.index(value) <= ladder.index(requested)]
    return below[-1] if below else in_ladder[0]


def compose_model_knobs(
    model: dict[str, Any],
    effort: dict[str, Any],
) -> dict[str, str]:
    """Merge the resolved effort onto a model's knobs, capability-clamped.

    ``resolve_desired_model`` only carries model-intrinsic knobs (opus thinking);
    without this merge the resolved effort is reported on the admit turn but never
    reaches the bridge, so every Auto-bound reasoner ran at its catalog default.
    """
    knobs: dict[str, str] = dict(model.get("model_knobs") or {})
    model_id = str(model.get("resolved_model_id") or "").strip()
    requested = str(effort.get("resolved_effort") or "").strip().lower()
    if not model_id or not requested:
        return knobs
    try:
        bare = canonical_cursor_bare_id(model_id)
    except ValueError:
        return knobs
    name = effort_knob_name(bare)
    if name is None:
        return knobs
    spec = supported_knobs(bare).get(name)
    if spec is None:
        return knobs
    value = _clamp_effort_to_accepted(requested, tuple(spec.accepted))
    if value is not None:
        knobs[name] = value
    return knobs


def resolve_handoff_contract(contract: str | None) -> str:
    """Map Auto ``request.contract`` → cursor-sdk ``handoff_contract``."""
    raw = (contract or "answer").strip().lower() or "answer"
    if raw == "implement":
        return "pure-mechanical"
    if raw == "execute":
        return "light-bounded"
    if raw == "propagate":
        return "light-bounded"
    if raw in {"investigate", "confer", "seed"}:
        return "light-bounded"
    if raw == "verify":
        return "light-bounded"
    return "light-bounded"
