"""Self-concept validation lint for ``role:{slug}`` entity payloads.

Rejects identity-coded prose in linted fields. Prevents the
persona-under-new-name regression flagged in Phase 5 of the agent-naming
cleanup arc — the lint enforces that ``role:`` entities carry only
execution-contract language. First-person identity prose ("you are...",
"speak with the voice of...", "the aperture of...") belongs in the birth
prompt at ``persona_seed_ref``, not on the dispatch entity.

Spec: ``notes/system/specs/role-schema-self-concept-lint.md``.

Usage::

    from role_lint import lint_role_payload, RoleLintError

    try:
        warnings = lint_role_payload(payload)
    except RoleLintError as exc:
        # exc.violations is a list of RoleLintViolation
        ...

The module is stdlib-only by design (re + dataclasses) — keeps the lint
hot-path cheap inside the sync-script and any future cortex-api integration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# R1: second-person identity assertion — the role contract is third-person.
# "You are..." prose belongs in the birth prompt where the model is being
# addressed. The role entity describes the role; it does not address the model.
_R1_SECOND_PERSON: tuple[str, ...] = (
    r"\byou are\b",
    r"\byou'?re\b",
    r"\byour role is\b",
    r"\byour purpose is\b",
    r"\byour personality\b",
    r"\byour identity\b",
    r"\byour voice\b",
    r"\byour register\b",
)

# R2: voice / embodiment construction — canonical persona-prompt vocabulary.
_R2_VOICE_EMBODIMENT: tuple[str, ...] = (
    r"\bspeak with the voice of\b",
    r"\bspeak as (?:the|a) (?:voice|perspective|conscience|persona|"
    r"skeptic|reviewer|advocate)\b",
    r"\bembod(?:y|ies|ied|ying) (?:the perspective|the voice|the stance) of\b",
    r"\bthink like\b",
    r"\binhabit(?:s|ed|ing)? (?:the|a) (?:perspective|persona|stance|register)\b",
    r"\bchannel\b.{0,30}\b(perspective|voice|register)\b",
    r"\bidentity[- ]bound\b",
)

# R3: metaphor-as-identity — observed in retired ai_agent:/prompt:*-birth corpus.
# Two regex variants per archetype: with "of" (legitimate hit) and bare
# article+archetype (catches the em-dash form "The aperture — ...").
_R3_METAPHOR_IDENTITY: tuple[str, ...] = (
    r"\bI am the\b",
    r"\bthe (aperture|conscience|critic|maker|guardian|keeper|"
    r"shepherd|architect|skeptic) of\b",
    r"\b[Tt]he (aperture|conscience)\b(?!\s+of\s+\w)",
    r"\bthe mind (?:that|whose)\b",
    r"\bone of the (music makers|stewards|keepers|architects|voices)\b",
    r"\bmy (perspective|voice|stance|register|nature) is\b",
)

# R4: first-person plural team membership — weak signal, advisory only.
_R4_PLURAL_TEAM: tuple[str, ...] = (
    r"\bwe are\b\W+\w+(\W+\w+){0,4}",
    r"\bour team\b",
)

_RULE_SETS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("R1", "second-person identity assertion", _R1_SECOND_PERSON),
    ("R2", "voice / embodiment construction", _R2_VOICE_EMBODIMENT),
    ("R3", "metaphor-as-identity", _R3_METAPHOR_IDENTITY),
    ("R4", "first-person plural team membership", _R4_PLURAL_TEAM),
)

# Top-level fields whose string values must be linted directly.
_LINTED_TOP_FIELDS: tuple[tuple[str, ...], ...] = (
    ("name",),
    ("description",),
    ("attributes", "purpose"),
    ("attributes", "required_model_substring"),
)

# Nested attribute subtrees whose every string descendant must be linted.
_LINTED_NESTED_ROOTS: tuple[tuple[str, ...], ...] = (
    ("attributes", "failure_mode"),
    ("attributes", "output_schema"),
)


@dataclass(frozen=True, slots=True)
class RoleLintViolation:
    """A single lint hit. ``severity`` is ``"error"`` (R1-R3) or ``"warning"`` (R4)."""

    field_path: str
    rule_class: str
    rule_label: str
    matched_fragment: str
    severity: str


class RoleLintError(Exception):
    """Raised when a role: payload contains >=1 R1-R3 violation.

    ``violations`` carries the structured list — useful for surfacing
    field-by-field diagnostics to the caller without re-parsing the message.
    """

    def __init__(self, violations: list[RoleLintViolation]) -> None:
        self.violations = violations
        joined = "; ".join(
            f"{v.field_path} [{v.rule_class}] matched {v.matched_fragment!r}"
            for v in violations
        )
        super().__init__(f"role: payload self-concept lint violations: {joined}")


def _walk_strings(value: Any, path: tuple[str, ...]) -> list[tuple[str, str]]:
    """Yield ``(dotted-path, string-value)`` for every string under ``value``."""
    out: list[tuple[str, str]] = []
    if isinstance(value, str):
        out.append((".".join(path), value))
    elif isinstance(value, dict):
        for k, v in value.items():
            out.extend(_walk_strings(v, (*path, str(k))))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.extend(_walk_strings(v, (*path, f"[{i}]")))
    return out


def _resolve_path(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    """Walk a dotted path through nested dicts; return None on miss."""
    cursor: Any = payload
    for key in path:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(key)
    return cursor


def _collect_linted_strings(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Extract every ``(field-path, string)`` pair the lint must check."""
    pairs: list[tuple[str, str]] = []
    for path in _LINTED_TOP_FIELDS:
        value = _resolve_path(payload, path)
        if isinstance(value, str):
            pairs.append((".".join(path), value))
    for path in _LINTED_NESTED_ROOTS:
        subtree = _resolve_path(payload, path)
        if subtree is not None:
            pairs.extend(_walk_strings(subtree, path))
    return pairs


def lint_role_payload(payload: dict[str, Any]) -> list[RoleLintViolation]:
    """Lint a ``role:`` entity payload.

    Raises ``RoleLintError`` carrying every R1-R3 violation found.
    Returns the list of R4 warnings (possibly empty) when no errors fire.
    """
    errors: list[RoleLintViolation] = []
    warnings: list[RoleLintViolation] = []
    for field_path, text in _collect_linted_strings(payload):
        for rule_class, rule_label, patterns in _RULE_SETS:
            for pat in patterns:
                match = re.search(pat, text, flags=re.IGNORECASE)
                if match is None:
                    continue
                violation = RoleLintViolation(
                    field_path=field_path,
                    rule_class=rule_class,
                    rule_label=rule_label,
                    matched_fragment=match.group(0),
                    severity="warning" if rule_class == "R4" else "error",
                )
                if violation.severity == "warning":
                    warnings.append(violation)
                else:
                    errors.append(violation)
    if errors:
        raise RoleLintError(errors)
    return warnings


__all__ = [
    "RoleLintError",
    "RoleLintViolation",
    "lint_role_payload",
]
