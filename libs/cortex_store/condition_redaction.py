"""Per-surface asymmetric redaction for condition entities.

Implements F3 from the condition entity-type spec (decision:ontology-standing-
condition, assertion 20685). Enforces the hard rules:

  R1  orchestrator/lead ALWAYS receives `full` for safety_invariant=true,
      regardless of reveal_default.
  R2  sub-agent dispatch payloads are ALWAYS at most `sanitized` for
      sensitive/restricted conditions.
  R3  When a surface would need hidden context to act safely, the redactor
      returns a CONFLICT sentinel; the surface MUST escalate — never silently
      use or omit the condition.

Surface keys: boot | retrieval | dispatch | advice | session_close | logs
Audience classes: orchestrator_lead | sub_agent | log_sink

Visibility levels (ordered strictest→most permissive): hidden < sanitized < full
"""

from __future__ import annotations

from typing import Final, Literal

# Sentinel returned when the surface cannot safely proceed without context it
# is not permitted to receive. Consumers MUST escalate this to the orchestrator
# rather than silently using or omitting the condition.
CONFLICT: Final[str] = "CONFLICT"

SurfaceKey = Literal["boot", "retrieval", "dispatch", "advice", "session_close", "logs"]
AudienceClass = Literal["orchestrator_lead", "sub_agent", "log_sink"]
VisibilityLevel = Literal["full", "sanitized", "hidden"]

_SURFACE_KEYS: frozenset[str] = frozenset(
    {"boot", "retrieval", "dispatch", "advice", "session_close", "logs"}
)

# Default visibility for each reveal_default × surface combination.
# reveal_default=open → full everywhere; sensitive → sanitized+hidden split;
# restricted → hidden to sub_agent and log_sink.
_REVEAL_DEFAULT_POLICY: dict[str, dict[str, VisibilityLevel]] = {
    "open": {s: "full" for s in _SURFACE_KEYS},
    "sensitive": {
        "boot": "full",
        "retrieval": "sanitized",
        "dispatch": "sanitized",
        "advice": "sanitized",
        "session_close": "sanitized",
        "logs": "hidden",
    },
    "restricted": {
        "boot": "sanitized",
        "retrieval": "sanitized",
        "dispatch": "hidden",
        "advice": "hidden",
        "session_close": "sanitized",
        "logs": "hidden",
    },
}


def _coarse_default(
    reveal_default: str, surface: str
) -> VisibilityLevel:
    policy = _REVEAL_DEFAULT_POLICY.get(reveal_default, _REVEAL_DEFAULT_POLICY["open"])
    return policy.get(surface, "sanitized")  # type: ignore[return-value]


def _visibility_for_surface(
    *,
    reveal_default: str,
    surface_visibility: dict[str, str] | None,
    safety_invariant: bool,
    surface: str,
    audience: AudienceClass,
) -> VisibilityLevel | str:
    """Compute the effective visibility for one (surface, audience) pair.

    Returns CONFLICT when the surface would need hidden-level context to act
    safely but the audience is not permitted full access.
    """
    # R1: safety_invariant + orchestrator_lead → always full
    if safety_invariant and audience == "orchestrator_lead":
        return "full"

    # Explicit per-surface override takes precedence over coarse default
    base: VisibilityLevel
    if surface_visibility and surface in surface_visibility:
        raw = surface_visibility[surface]
        base = raw if raw in ("full", "sanitized", "hidden") else _coarse_default(reveal_default, surface)  # type: ignore[assignment]
    else:
        base = _coarse_default(reveal_default, surface)

    # R2: sub_agent ALWAYS at most sanitized for sensitive/restricted
    if audience == "sub_agent" and reveal_default in ("sensitive", "restricted"):
        if base == "full":
            base = "sanitized"

    # R3: if the surface would need full/hidden context for safe operation but
    # can only receive hidden, escalate as CONFLICT.
    if base == "hidden" and safety_invariant and audience != "orchestrator_lead":
        return CONFLICT

    return base


def redact(
    *,
    reveal_default: str,
    surface_visibility: dict[str, str] | None,
    safety_invariant: bool,
    surface: str,
    audience: AudienceClass,
) -> VisibilityLevel | str:
    """Compute effective visibility level for a surface + audience pair.

    Returns one of: "full" | "sanitized" | "hidden" | CONFLICT.

    CONFLICT means the surface cannot safely operate on this condition without
    context it is not permitted to receive. Callers MUST escalate; they MUST
    NOT silently use or omit the condition.
    """
    if surface not in _SURFACE_KEYS:
        raise ValueError(
            f"Unknown surface {surface!r}. Must be one of {sorted(_SURFACE_KEYS)}"
        )
    return _visibility_for_surface(
        reveal_default=reveal_default,
        surface_visibility=surface_visibility,
        safety_invariant=safety_invariant,
        surface=surface,
        audience=audience,
    )


def redact_all_surfaces(
    *,
    reveal_default: str,
    surface_visibility: dict[str, str] | None,
    safety_invariant: bool,
    audience: AudienceClass,
) -> dict[str, VisibilityLevel | str]:
    """Return the effective visibility for all six surfaces for one audience class.

    Keys: boot, retrieval, dispatch, advice, session_close, logs.
    Values: "full" | "sanitized" | "hidden" | CONFLICT.
    """
    return {
        surface: redact(
            reveal_default=reveal_default,
            surface_visibility=surface_visibility,
            safety_invariant=safety_invariant,
            surface=surface,
            audience=audience,
        )
        for surface in sorted(_SURFACE_KEYS)
    }


def apply_condition_to_payload(
    condition_attrs: dict[str, object],
    payload: dict[str, object],
    *,
    surface: str,
    audience: AudienceClass,
) -> dict[str, object]:
    """Project condition attributes into *payload* at the appropriate redaction level.

    Mutates a copy of *payload* with a ``conditions`` key containing either the
    sanitized/full condition data or a CONFLICT escalation marker.

    Sanitized projection omits ``narrative`` body (retains slug, lifecycle, and
    a one-line ``narrative_head``). Full projection includes all attributes.
    """
    reveal_default = str(condition_attrs.get("reveal_default", "open"))
    sv_raw = condition_attrs.get("surface_visibility")
    surface_visibility = sv_raw if isinstance(sv_raw, dict) else None
    safety_invariant = bool(condition_attrs.get("safety_invariant", False))

    level = redact(
        reveal_default=reveal_default,
        surface_visibility=surface_visibility,
        safety_invariant=safety_invariant,
        surface=surface,
        audience=audience,
    )

    out = dict(payload)
    if level == CONFLICT:
        out["condition_redaction_conflict"] = {
            "surface": surface,
            "audience": audience,
            "action_required": "ESCALATE_TO_ORCHESTRATOR",
            "reason": (
                "Safety-invariant condition is hidden at this surface for this audience "
                "but required for safe operation. Do not proceed; escalate to the "
                "orchestrator/lead for resolution."
            ),
        }
        return out

    if level == "hidden":
        return out  # condition not surfaced at all

    narrative = str(condition_attrs.get("narrative", ""))
    narrative_head = narrative.split("\n")[0][:200] if narrative else ""

    if level == "sanitized":
        projected = {
            "lifecycle": condition_attrs.get("lifecycle"),
            "reveal_default": reveal_default,
            "safety_invariant": safety_invariant,
            "narrative_head": narrative_head,
        }
    else:  # full
        projected = dict(condition_attrs)

    conditions_key = out.setdefault("conditions", [])
    if isinstance(conditions_key, list):
        conditions_key.append(projected)  # type: ignore[union-attr]
    return out


__all__ = [
    "CONFLICT",
    "apply_condition_to_payload",
    "redact",
    "redact_all_surfaces",
]
