"""Pure wire-map for agent_bus.request desired_model / effort / contract.

Inline projection of dense-spec Impl §4 — no I/O. Unit-tested.
"""

from __future__ import annotations

from typing import Any, Literal

from contract_vocab import CANONICAL_CONTRACTS
from effort_vocabulary import AUTO_EFFORT, WIRE_LADDER, normalize_effort

Contract = Literal[
    "answer",
    "confer",
    "ask",
    "investigate",
    "implement",
    "verify",
    "execute",
    "propagate",
    "seed",
    "recon",
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
    "sonnet-5": "cursor/claude-sonnet-5",
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
# Judgment: opus + fable. ``cdp/sonnet-5`` is a producer alias, not a binder.
BINDABLE_CDP_ESCALATIONS: tuple[str, ...] = (
    "cdp/opus-5",
    "cdp/fable",
    "cdp/sonnet-5",
)
_CONTRACT_EFFORT_DEFAULTS: dict[str, str] = {
    "investigate": "xhigh",
    "confer": "xhigh",
    "seed": "xhigh",
    "verify": "xhigh",
    "execute": "xhigh",
    "propagate": "xhigh",
    "implement": "medium",
    "recon": "medium",
    "ask": "medium",
    "answer": "medium",
}
_JUDGMENT_HANDOFF = "light-bounded"


def _effort_omitted(desired_effort: str | None) -> bool:
    raw = ("" if desired_effort is None else str(desired_effort)).strip().lower()
    return raw == "" or raw == AUTO_EFFORT
# Life seats often put CDP models on desired_model by mistake; map → escalation.
_CDP_DESIRED_MODEL_ALIASES: dict[str, str] = {
    "cdp/opus-5": "cdp/opus-5",
    "cdp/opus": "cdp/opus-5",
    "cdp/opus5": "cdp/opus-5",
    "cdp/fable": "cdp/fable",
    "cdp/fable-5": "cdp/fable",
    "cdp/fable5": "cdp/fable",
    "cdp/sonnet-5": "cdp/sonnet-5",
    "cdp/sonnet": "cdp/sonnet-5",
    "cdp/sonnet5": "cdp/sonnet-5",
}
_CONTRACTS = frozenset(CANONICAL_CONTRACTS)


def resolve_desired_model(
    desired_model: str | None,
    *,
    contract: str = "answer",
) -> dict[str, Any]:
    """Map request ``desired_model`` hint → resolved ``model_id`` + notes.

    ``auto`` (default) picks by contract: judgment and mechanical paths both
    default to Composer (answer/confer/investigate/seed/light-bounded,
    implement/verify/ask/recon). Grok is experimental explicit pin only.
    Explicit hints are honored and reported.
    Other Models (Sonnet/Opus/Terra) are never the auto default — they draw
    Cursor's capped second pool; unattended judgment stays on Cursor Models.
    """
    raw = (desired_model or "auto").strip().lower() or "auto"
    if raw == "auto":
        by_contract = {
            "answer": "cursor/composer-2.5",
            "confer": "cursor/composer-2.5",
            "ask": "cursor/composer-2.5",
            "investigate": "cursor/composer-2.5",
            "implement": "cursor/composer-2.5",
            "verify": "cursor/composer-2.5",
            "seed": "cursor/composer-2.5",
            "recon": "cursor/composer-2.5",
            "light-bounded": "cursor/composer-2.5",
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

    Values ``cdp/opus-5``, ``cdp/fable``, and producer alias ``cdp/sonnet-5``
    honor; absent/empty ⇒ no CDP leg; unknown ⇒ ``rejected`` for admit refusal.
    """
    raw = (escalation or "").strip().lower()
    if not raw:
        return {
            "requested": "",
            "resolved_escalation": None,
            "honored": False,
            "notes": "no escalation requested",
        }
    resolved = coerce_cdp_desired_model_alias(raw) or raw
    if resolved in BINDABLE_CDP_ESCALATIONS:
        return {
            "requested": raw,
            "resolved_escalation": resolved,
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


def resolve_desired_effort(
    desired_effort: str | None,
    *,
    contract: str = "answer",
    handoff_contract: str | None = None,
) -> dict[str, Any]:
    """Normalize + clamp ``desired_effort`` to canonical wire values.

    Canonical set: low|medium|high|xhigh|max. Surface aliases (``extra``,
    ``extra-high``, ``Extra High``) normalize to ``xhigh`` via effort_vocabulary.

    ``auto``/omitted ⇒ per-contract default: investigate/confer/seed/verify/execute/
    propagate → ``xhigh``; implement/recon/ask/answer → ``medium``; ``implement``
    whose handoff contract is ``light-bounded`` (body declares judgment) → ``xhigh``.
    ``requested`` echoes ``auto`` so the admit turn surfaces the rule via
    ``admit_effort_override_rule_line``.
    """
    contract_key = (contract or "answer").strip().lower()
    if _effort_omitted(desired_effort):
        if contract_key == "implement" and handoff_contract == _JUDGMENT_HANDOFF:
            resolved = "xhigh"
            why = f"auto chose xhigh for contract=implement (handoff={_JUDGMENT_HANDOFF})"
        else:
            resolved = _CONTRACT_EFFORT_DEFAULTS.get(contract_key, "medium")
            why = f"auto chose {resolved} for contract={contract_key}"
        return {
            "requested": AUTO_EFFORT,
            "resolved_effort": resolved,
            "clamped": False,
            "notes": why,
        }
    requested = str(desired_effort).strip().lower()
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
        "ask": "dispatched-and-relayed",
        "investigate": "dispatched-and-relayed",
        "implement": "dispatched-and-relayed",
        "verify": "dispatched-and-relayed",
        "execute": "executed",
        "propagate": "propagated",
        "seed": "dispatched-and-relayed",
        "recon": "dispatched-and-relayed",
    }
    return {
        "requested": raw,
        "contract": raw,
        "disposition_hint": hints[raw],
        "notes": "ok",
    }


def resolve_handoff_contract(
    contract: str | None,
    body: str | None = None,
) -> str:
    """Map Auto ``request.contract`` → cursor-sdk ``handoff_contract``.

    Unmarked ``implement`` (no body, or body without judgment markers) stays
    ``pure-mechanical``. A body that declares judgment raises to
    ``light-bounded`` — the existing non-mechanical token — without adding a
    member to ``REASONING_POSTURE_SKIP_CONTRACTS``.
    """
    raw = (contract or "answer").strip().lower() or "answer"
    if raw == "implement":
        if body:
            from services.git_integration_worker.cursor_auto.directive import (
                body_declares_judgment,
            )

            if body_declares_judgment(body):
                return "light-bounded"
        return "pure-mechanical"
    if raw == "execute":
        return "light-bounded"
    if raw == "propagate":
        return "light-bounded"
    if raw == "ask":
        return "ask"
    if raw in {"investigate", "confer", "seed"}:
        return "light-bounded"
    if raw == "verify":
        return "light-bounded"
    return "light-bounded"
