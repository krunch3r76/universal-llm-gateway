"""Pattern A impresario policy — sole reader of ``route_policy.yaml`` ``impresario:``."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from cursor_capabilities import CURSOR_MODEL_CAPABILITIES, canonical_cursor_bare_id
from effort_vocabulary import WIRE_LADDER, normalize_effort
from universal_protocol.errors import ProtocolError

from implement_admission.routing import default_policy_path, load_route_policy

Transport = Literal["cdp_parked", "cursor_nested", "grok_bot_window"]

ALLOWED_UNDER_SUBSTRATES = frozenset({"cursor-sdk", "grok-bot", "hybrid"})
ALLOWED_SURFACES = frozenset({"cursor-ide", "grokbot", "maestro"})
RESTORE_MODEL = "cdp/fable-5.1"
_HYBRID_MECHANICAL_MODEL = "cursor/composer-2.5"
_HYBRID_MECHANICAL_SEAT = "cursor-sdk"
_HYBRID_MECHANICAL_EFFORT = "medium"
ADVISOR_MODEL = "cursor/gemini-4-argon"
ADVISOR_LEAN_DISPATCH_LINE = "Use the lean-dispatch skill"
ADVISOR_FALLBACK_MODEL = "cursor/grok-4.7"
_ARGON_BARRED_IMPRESARIO_ROLES = frozenset(
    {
        "critic",
        "sketch",
        "skeptic",
        "review",
        "ear",
        "ear_patrol",
        "implement",
        "sweep",
        "mechanical",
        "driver",
    }
)


@dataclass(frozen=True, slots=True)
class SeatSpec:
    """Resolved seat with derived transport (never stored in YAML)."""

    model: str
    effort_rung: str
    transport: Transport
    seat: str = "cursor-sdk"
    fast: bool = False


@dataclass(frozen=True, slots=True)
class AdvisorSpec:
    """Advisor duty — consult-only; not in ``allowed_critics``."""

    seat: SeatSpec
    paused: bool
    role_sentence: str
    triggers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdvisorAdmitResult:
    """Pure advisor-route admission outcome (no model I/O)."""

    admitted: bool
    resolved_model: str | None
    code: str | None = None
    message: str | None = None
    provider_fallback: bool = False
    event_signal: str | None = None
    event_payload: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ImpresarioPolicy:
    """Frozen Pattern A policy snapshot."""

    pattern: str
    pattern_version: str
    under_substrate: str
    allowed_under_substrates: tuple[str, ...]
    driver: SeatSpec
    mechanical: SeatSpec
    critic: SeatSpec
    critic_roles: Mapping[str, dict[str, Any]]
    allowed_critics: frozenset[str]
    cdp_reroute_for_critic: bool
    ear: SeatSpec
    ear_patrol: SeatSpec
    advisor: AdvisorSpec
    policy_sha256: str


@dataclass(frozen=True, slots=True)
class ResolvedRoles:
    """Role seats after ``resolve_roles()`` — no import-time constants."""

    driver: SeatSpec
    mechanical: SeatSpec
    critic: SeatSpec
    sketch: SeatSpec
    skeptic: SeatSpec
    review: SeatSpec
    restore: SeatSpec
    policy: ImpresarioPolicy


def _derive_transport(model: str) -> Transport:
    if model.startswith("cdp/"):
        return "cdp_parked"
    if model.startswith("cursor/"):
        return "cursor_nested"
    msg = f"impresario model {model!r} has no derived transport"
    raise ValueError(msg)


def _seat(model: str, effort: str, *, seat: str = "cursor-sdk") -> SeatSpec:
    rung = normalize_effort(effort) or effort.strip().lower()
    return SeatSpec(
        model=model.strip(),
        effort_rung=rung,
        transport=_derive_transport(model),
        seat=seat,
    )


def _canonical_impresario_block(policy: dict[str, Any]) -> str:
    block = policy.get("impresario")
    if not isinstance(block, dict):
        return ""
    return yaml.safe_dump(
        {"impresario": block}, sort_keys=True, default_flow_style=False
    )


def policy_snapshot_sha256(*, path: Path | None = None) -> str:
    """Sha256 of canonical ``impresario:`` block as loaded by this module."""
    policy = load_route_policy(path)
    return hashlib.sha256(_canonical_impresario_block(policy).encode()).hexdigest()


def _parse_seat_entry(
    raw: object,
    *,
    default_model: str,
    default_effort: str,
    require_allowed_critic: bool = True,
    allowed_critics: frozenset[str] | None = None,
) -> SeatSpec:
    if not isinstance(raw, dict):
        return _seat(default_model, default_effort)
    model = str(raw.get("model") or default_model).strip()
    effort = str(raw.get("effort") or default_effort).strip()
    seat = str(raw.get("seat") or "cursor-sdk").strip()
    fast = bool(raw.get("fast"))
    resolved = _seat(model, effort, seat=seat)
    if require_allowed_critic and allowed_critics is not None:
        if model not in allowed_critics:
            msg = f"impresario seat model {model!r} not in allowed_critics"
            raise ValueError(msg)
    return SeatSpec(
        model=resolved.model,
        effort_rung=resolved.effort_rung,
        transport=resolved.transport,
        seat=resolved.seat,
        fast=fast,
    )


def parse_impresario_policy(policy: dict[str, Any]) -> ImpresarioPolicy:
    """Parse and validate the ``impresario`` block; raise ``ValueError`` when invalid."""
    raw = policy.get("impresario")
    if not isinstance(raw, dict):
        msg = "route policy missing impresario mapping"
        raise ValueError(msg)
    under = str(raw.get("under_substrate") or "cursor-sdk").strip()
    if under not in ALLOWED_UNDER_SUBSTRATES:
        msg = f"impresario.under_substrate {under!r} not in {sorted(ALLOWED_UNDER_SUBSTRATES)!r}"
        raise ValueError(msg)
    allowed_under = raw.get("allowed_under_substrates") or list(
        ALLOWED_UNDER_SUBSTRATES
    )
    if not isinstance(allowed_under, list):
        msg = "impresario.allowed_under_substrates must be a list"
        raise ValueError(msg)
    allowed_under_tuple = tuple(str(x).strip() for x in allowed_under)
    driver_raw = raw.get("driver") or {}
    mech_raw = raw.get("mechanical") or {}
    critic_raw = raw.get("critic") or {}
    driver = _parse_seat_entry(
        driver_raw, default_model="cursor/grok-4.7", default_effort="high"
    )
    mechanical = _parse_seat_entry(
        mech_raw,
        default_model=_HYBRID_MECHANICAL_MODEL,
        default_effort=_HYBRID_MECHANICAL_EFFORT,
    )
    critic = _parse_seat_entry(
        critic_raw,
        default_model="cdp/opus-5.5",
        default_effort="high",
        allowed_critics=frozenset(),  # set below after allowed_critics parsed
        require_allowed_critic=False,
    )
    allowed_raw = raw.get("allowed_critics") or []
    if not isinstance(allowed_raw, list) or not allowed_raw:
        msg = "impresario.allowed_critics must be a non-empty list"
        raise ValueError(msg)
    allowed_critics = frozenset(str(m).strip() for m in allowed_raw)
    ear_raw = raw.get("ear") or {}
    ear_patrol_raw = raw.get("ear_patrol") or {}
    ear = _parse_seat_entry(
        ear_raw,
        default_model=critic.model,
        default_effort=critic.effort_rung,
        allowed_critics=allowed_critics,
    )
    ear_patrol = _parse_seat_entry(
        ear_patrol_raw,
        default_model="cursor/composer-2.5",
        default_effort="low",
        require_allowed_critic=False,
    )
    advisor_raw = raw.get("advisor") or {}
    if not isinstance(advisor_raw, dict):
        msg = "impresario.advisor must be a mapping"
        raise ValueError(msg)
    advisor_seat = _parse_seat_entry(
        advisor_raw,
        default_model=ADVISOR_MODEL,
        default_effort="high",
        require_allowed_critic=False,
    )
    advisor_fast = bool(advisor_raw.get("fast"))
    advisor_seat = SeatSpec(
        model=advisor_seat.model,
        effort_rung=advisor_seat.effort_rung,
        transport=advisor_seat.transport,
        seat=advisor_seat.seat,
        fast=advisor_fast,
    )
    if advisor_seat.model != ADVISOR_MODEL:
        msg = f"impresario.advisor.model must be {ADVISOR_MODEL!r}"
        raise ValueError(msg)
    role_sentence = str(
        advisor_raw.get("role_sentence")
        or "You advise the next move. You do not author the change and you do not review it."
    ).strip()
    triggers_raw = advisor_raw.get("triggers") or []
    if not isinstance(triggers_raw, list) or len(triggers_raw) < 1:
        msg = "impresario.advisor.triggers must be a non-empty list"
        raise ValueError(msg)
    advisor_triggers = tuple(str(t).strip() for t in triggers_raw if str(t).strip())
    advisor = AdvisorSpec(
        seat=advisor_seat,
        paused=bool(advisor_raw.get("paused")),
        role_sentence=role_sentence,
        triggers=advisor_triggers,
    )
    roles_raw = raw.get("critic_roles") or {}
    if not isinstance(roles_raw, dict):
        msg = "impresario.critic_roles must be a mapping"
        raise ValueError(msg)
    for model in (driver.model, mechanical.model, critic.model):
        if model.startswith("cursor/"):
            bare = canonical_cursor_bare_id(model)
            if bare not in CURSOR_MODEL_CAPABILITIES:
                msg = (
                    f"impresario seat model {model!r} not in CURSOR_MODEL_CAPABILITIES"
                )
                raise ValueError(msg)
    for model in allowed_critics:
        if model.startswith("cursor/"):
            bare = canonical_cursor_bare_id(model)
            if bare not in CURSOR_MODEL_CAPABILITIES:
                msg = f"allowed critic {model!r} not in CURSOR_MODEL_CAPABILITIES"
                raise ValueError(msg)
    sha = hashlib.sha256(_canonical_impresario_block(policy).encode()).hexdigest()
    return ImpresarioPolicy(
        pattern=str(raw.get("pattern") or "pattern-a"),
        pattern_version=str(raw.get("pattern_version") or "1"),
        under_substrate=under,
        allowed_under_substrates=allowed_under_tuple,
        driver=driver,
        mechanical=mechanical,
        critic=critic,
        critic_roles=roles_raw,
        allowed_critics=allowed_critics,
        cdp_reroute_for_critic=bool(raw.get("cdp_reroute_for_critic")),
        ear=ear,
        ear_patrol=ear_patrol,
        advisor=advisor,
        policy_sha256=sha,
    )


def critic_for_role(policy: ImpresarioPolicy, role: str) -> SeatSpec:
    """Resolve ear vs critic role seats (``ear`` / ``ear_patrol`` are not critic_roles)."""
    if role == "ear":
        return policy.ear
    if role == "ear_patrol":
        return policy.ear_patrol
    return _role_seat(policy, role)


@lru_cache(maxsize=4)
def load_impresario_policy(path: Path | None = None) -> ImpresarioPolicy:
    """Process-cached load of Pattern A policy."""
    policy_path = path or default_policy_path()
    return parse_impresario_policy(load_route_policy(policy_path))


def impresario_registry_errors(policy: dict[str, Any]) -> list[str]:
    """Validate impresario block for ``registry_errors`` — never raises."""
    errors: list[str] = []
    try:
        parsed = parse_impresario_policy(policy)
    except ValueError as exc:
        errors.append(str(exc))
        return errors
    if parsed.under_substrate == "hybrid":
        mech = parsed.mechanical
        if (
            mech.model != _HYBRID_MECHANICAL_MODEL
            or mech.seat != _HYBRID_MECHANICAL_SEAT
        ):
            errors.append(
                "under_substrate hybrid requires impresario.mechanical "
                f"model {_HYBRID_MECHANICAL_MODEL!r} on seat {_HYBRID_MECHANICAL_SEAT!r}"
            )
        workflows = policy.get("workflows")
        if isinstance(workflows, dict):
            wf = workflows.get("mechanical_implement")
            if isinstance(wf, dict):
                wf_model = wf.get("model")
                if (
                    isinstance(wf_model, str)
                    and wf_model.strip() != _HYBRID_MECHANICAL_MODEL
                ):
                    errors.append(
                        "under_substrate hybrid requires "
                        f"workflows.mechanical_implement.model {_HYBRID_MECHANICAL_MODEL!r}"
                    )
                wf_seat = wf.get("seat")
                if isinstance(wf_seat, str) and wf_seat.strip().startswith("grok-bot"):
                    errors.append(
                        "under_substrate hybrid forbids grok-bot seat on "
                        "workflows.mechanical_implement"
                    )
    for model in parsed.allowed_critics:
        if model not in parsed.allowed_critics:
            continue
        if model.startswith("cdp/") or model.startswith("cursor/"):
            continue
        errors.append(f"impresario allowed critic {model!r} has unknown prefix")
    if parsed.critic.model not in parsed.allowed_critics:
        errors.append(
            f"impresario.critic.model {parsed.critic.model!r} not in allowed_critics"
        )
    return errors


def _validate_effort_for_model(model: str, canonical_rung: str) -> None:
    if canonical_rung not in WIRE_LADDER:
        raise ProtocolError(
            code="CRITIC_EFFORT_UNSUPPORTED",
            message=f"effort rung {canonical_rung!r} not in wire ladder",
            source="rpc",
            retryable=False,
            data={"model": model, "effort": canonical_rung},
        )
    if not model.startswith("cursor/"):
        return
    bare = canonical_cursor_bare_id(model)
    cap = CURSOR_MODEL_CAPABILITIES.get(bare)
    if cap is None:
        return
    effort_knob = cap.knobs.get("effort")
    if effort_knob is None:
        return
    wire_effort = canonical_rung
    if wire_effort == "extra":
        wire_effort = "xhigh"
    if wire_effort == "max" and "max" not in effort_knob.accepted:
        if "xhigh" in effort_knob.accepted:
            return
    if (
        wire_effort not in effort_knob.accepted
        and canonical_rung not in effort_knob.accepted
    ):
        raise ProtocolError(
            code="CRITIC_EFFORT_UNSUPPORTED",
            message=f"model {model!r} does not accept effort {canonical_rung!r}",
            source="rpc",
            retryable=False,
            data={"model": model, "effort": canonical_rung},
        )


def _role_seat(
    policy: ImpresarioPolicy,
    role: str,
    *,
    critic_override: SeatSpec | None = None,
) -> SeatSpec:
    base = critic_override or policy.critic
    raw = policy.critic_roles.get(role)
    if not isinstance(raw, dict) or not raw:
        return _seat(base.model, base.effort_rung, seat=base.seat)
    model = str(raw.get("model") or base.model).strip()
    effort = str(raw.get("effort") or base.effort_rung).strip()
    seat = str(raw.get("seat") or base.seat).strip()
    resolved = _seat(model, effort, seat=seat)
    if model not in policy.allowed_critics:
        raise ProtocolError(
            code="CRITIC_MODEL_UNSUPPORTED",
            message=f"critic role {role!r} model {model!r} not in allowed_critics",
            source="rpc",
            retryable=False,
        )
    return resolved


def resolve_roles(
    policy: ImpresarioPolicy | None = None,
    *,
    critic_model: str | None = None,
    critic_effort: str | None = None,
) -> ResolvedRoles:
    """Resolve Pattern A role seats (no import-time ACTIVE/SKEPTIC constants)."""
    pol = policy or load_impresario_policy()
    critic = pol.critic
    if critic_model:
        effort = critic_effort or critic.effort_rung
        critic = _seat(critic_model, effort, seat=critic.seat)
        if critic_model not in pol.allowed_critics:
            raise ProtocolError(
                code="CRITIC_MODEL_UNSUPPORTED",
                message=f"critic model {critic_model!r} not in allowed_critics",
                source="rpc",
                retryable=False,
            )
    elif critic_effort:
        critic = _seat(critic.model, critic_effort, seat=critic.seat)
    _validate_effort_for_model(critic.model, critic.effort_rung)
    mechanical = pol.mechanical
    if pol.under_substrate == "hybrid":
        mechanical = _seat(
            _HYBRID_MECHANICAL_MODEL,
            _HYBRID_MECHANICAL_EFFORT,
            seat=_HYBRID_MECHANICAL_SEAT,
        )
    sketch = _role_seat(pol, "sketch", critic_override=critic)
    skeptic = _role_seat(pol, "skeptic", critic_override=critic)
    review = _role_seat(pol, "review", critic_override=critic)
    restore = _seat(RESTORE_MODEL, "high")
    return ResolvedRoles(
        driver=pol.driver,
        mechanical=mechanical,
        critic=critic,
        sketch=sketch,
        skeptic=skeptic,
        review=review,
        restore=restore,
        policy=pol,
    )


def argon_barred_for_role(impresario_role: str | None) -> bool:
    """True when ``impresario_role`` must not use ``cursor/gemini-4-argon``."""
    if not impresario_role:
        return False
    return impresario_role.strip().lower() in _ARGON_BARRED_IMPRESARIO_ROLES


def refuse_argon_non_advisor_route(
    *,
    model: str,
    impresario_role: str | None,
) -> AdvisorAdmitResult | None:
    """Refuse argon when the role is critic, ear, implement, or sweep."""
    normalized = (model or "").strip()
    if normalized != ADVISOR_MODEL:
        return None
    role = (impresario_role or "").strip().lower()
    if role == "advisor":
        return None
    if argon_barred_for_role(impresario_role):
        return AdvisorAdmitResult(
            admitted=False,
            resolved_model=None,
            code="ARGON_ROLE_BARRED",
            message=f"cursor/gemini-4-argon is barred for impresario role {impresario_role!r}",
        )
    return None


def resolve_advisor_provider_fallback(
    *,
    provider_error: str,
    policy: ImpresarioPolicy | None = None,
) -> AdvisorAdmitResult:
    """Advisor-leg provider_error → grok fallback + event payload (not a calendar pause)."""
    _ = policy or load_impresario_policy()
    return AdvisorAdmitResult(
        admitted=True,
        resolved_model=ADVISOR_FALLBACK_MODEL,
        code="ADVISOR_PROVIDER_FALLBACK",
        message=provider_error.strip(),
        provider_fallback=True,
        event_signal="impresario.advisor.provider_fallback",
        event_payload={
            "requested_model": ADVISOR_MODEL,
            "resolved_model": ADVISOR_FALLBACK_MODEL,
            "provider_error": provider_error.strip(),
        },
    )


def evaluate_advisor_dispatch_admission(
    *,
    model: str,
    packet_body: str,
    item_author: str | None = None,
    impresario_role: str | None = "advisor",
    policy: ImpresarioPolicy | None = None,
    provider_error: str | None = None,
) -> AdvisorAdmitResult:
    """Pure advisor admission — no live model call."""
    pol = policy or load_impresario_policy()
    barred = refuse_argon_non_advisor_route(
        model=model, impresario_role=impresario_role
    )
    if barred is not None:
        return barred
    normalized = (model or "").strip()
    if normalized != ADVISOR_MODEL:
        return AdvisorAdmitResult(
            admitted=False,
            resolved_model=None,
            code="NOT_ADVISOR_MODEL",
            message=f"model {model!r} is not the advisor wire",
        )
    if provider_error and provider_error.strip():
        return resolve_advisor_provider_fallback(
            provider_error=provider_error, policy=pol
        )
    if pol.advisor.paused:
        return AdvisorAdmitResult(
            admitted=False,
            resolved_model=None,
            code="ADVISOR_PAUSED",
            message="impresario.advisor.paused is true",
        )
    if ADVISOR_LEAN_DISPATCH_LINE not in (packet_body or ""):
        return AdvisorAdmitResult(
            admitted=False,
            resolved_model=None,
            code="ADVISOR_LEAN_DISPATCH_REQUIRED",
            message=f"advisor packet must contain {ADVISOR_LEAN_DISPATCH_LINE!r}",
        )
    author = (item_author or "").strip()
    if author == ADVISOR_MODEL:
        return AdvisorAdmitResult(
            admitted=False,
            resolved_model=None,
            code="ADVISOR_K40_AUTHOR",
            message="advisor route refuses when the item author is cursor/gemini-4-argon",
        )
    return AdvisorAdmitResult(
        admitted=True,
        resolved_model=pol.advisor.seat.model,
        code=None,
        message=None,
    )
