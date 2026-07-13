"""Intent-object validation — closed verb, refuse-list, refs, one-round questions."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .registry import LifeIntentRegistry, load_registry

RefResolver = Callable[[str], bool]


class ReadOnlyRefResolver(Protocol):
    def __call__(self, ref: str) -> bool: ...


@dataclass(frozen=True)
class IntentReject:
    code: str
    detail: str


@dataclass(frozen=True)
class IntentCheckResult:
    normalized_intent: dict[str, Any] | None
    rejects: tuple[IntentReject, ...]
    questions: tuple[str, ...]


def _combined_text(intent: dict[str, Any]) -> str:
    parts = [
        str(intent.get("verb") or ""),
        str(intent.get("subject") or ""),
        str(intent.get("detail") or ""),
    ]
    return " ".join(parts).lower()


def _field_shape_rejects(intent: dict[str, Any], registry: LifeIntentRegistry) -> list[IntentReject]:
    rejects: list[IntentReject] = []
    verb = intent.get("verb")
    if not isinstance(verb, str) or not verb.strip():
        rejects.append(IntentReject("field_shape", "verb is required"))
        return rejects

    subject = intent.get("subject")
    if not isinstance(subject, str):
        rejects.append(IntentReject("field_shape", "subject must be a string"))
    elif len(subject.strip()) < 3:
        rejects.append(IntentReject("field_shape", "subject is too short"))

    detail = intent.get("detail")
    if not isinstance(detail, str):
        rejects.append(IntentReject("field_shape", "detail must be a string"))
    elif len(detail.strip()) < 10:
        rejects.append(IntentReject("field_shape", "detail is too short"))

    refs = intent.get("refs")
    if refs is not None:
        if not isinstance(refs, list) or any(not isinstance(r, str) for r in refs):
            rejects.append(IntentReject("field_shape", "refs must be a list of strings"))

    urgency = intent.get("urgency", "normal")
    if urgency is not None and str(urgency) not in registry.urgency_values:
        rejects.append(IntentReject("field_shape", "urgency must be normal or soon"))

    return rejects


def _refuse_list_rejects(text: str, registry: LifeIntentRegistry) -> list[IntentReject]:
    rejects: list[IntentReject] = []
    for token in registry.refuse_list:
        if token in text:
            rejects.append(
                IntentReject(
                    "refused_vocabulary",
                    "Intent contains vocabulary that must be routed through the code seat.",
                )
            )
            break
    return rejects


def _hard_out_rejects(text: str, registry: LifeIntentRegistry) -> list[IntentReject]:
    rejects: list[IntentReject] = []
    for entry in registry.hard_out_patterns:
        if re.search(entry.pattern, text, flags=re.IGNORECASE):
            rejects.append(IntentReject(entry.code, entry.detail))
            break
    return rejects


def _ref_rejects(
    intent: dict[str, Any],
    resolver: RefResolver | None,
) -> list[IntentReject]:
    if resolver is None:
        return []
    refs = intent.get("refs") or []
    if not isinstance(refs, list):
        return []
    rejects: list[IntentReject] = []
    for ref in refs:
        if not isinstance(ref, str) or not ref.strip():
            rejects.append(IntentReject("bad_ref", "refs entries must be non-empty strings"))
            continue
        if not resolver(ref):
            rejects.append(IntentReject("bad_ref", f"Reference could not be resolved: {ref}"))
    return rejects


def _unknown_verb_reject(verb: str, registry: LifeIntentRegistry) -> IntentReject | None:
    if verb not in registry.verbs:
        return IntentReject("unknown_verb", f"Unknown intent verb: {verb}")
    return None


def _questions_for_under_specified(intent: dict[str, Any]) -> tuple[str, ...]:
    subject = str(intent.get("subject") or "").strip()
    detail = str(intent.get("detail") or "").strip()
    if len(subject) >= 3 and len(detail) >= 10:
        vague_markers = ("thing", "stuff", "something", "it", "this", "that")
        subj_words = subject.lower().split()
        if len(subj_words) <= 2 and any(w in vague_markers for w in subj_words):
            return (
                "Can you name the specific system, feature, or file the subject refers to?",
            )
        if len(detail.split()) < 8:
            return (
                "Can you add one more sentence about what you observed or what outcome you want?",
            )
    return ()


def normalize_intent(intent: dict[str, Any], registry: LifeIntentRegistry) -> dict[str, Any]:
    verb = str(intent.get("verb") or "").strip().lower()
    urgency = str(intent.get("urgency") or "normal").strip().lower()
    if urgency not in registry.urgency_values:
        urgency = "normal"
    refs_raw = intent.get("refs") or []
    refs = [str(r).strip() for r in refs_raw if isinstance(r, str) and r.strip()]
    return {
        "verb": verb,
        "subject": str(intent.get("subject") or "").strip(),
        "detail": str(intent.get("detail") or "").strip(),
        "refs": refs,
        "urgency": urgency,
    }


def check_intent(
    intent: dict[str, Any],
    registry: LifeIntentRegistry | None = None,
    *,
    ref_resolver: RefResolver | None = None,
) -> IntentCheckResult:
    """Validate intent object; at most one questions round when under-specified."""
    reg = registry or load_registry()
    shape_rejects = _field_shape_rejects(intent, reg)
    if shape_rejects:
        return IntentCheckResult(None, tuple(shape_rejects), ())

    normalized = normalize_intent(intent, reg)
    text = _combined_text(normalized)

    hard_outs = _hard_out_rejects(text, reg)
    if hard_outs:
        return IntentCheckResult(None, tuple(hard_outs), ())

    refuse_hits = _refuse_list_rejects(text, reg)
    if refuse_hits:
        return IntentCheckResult(None, tuple(refuse_hits), ())

    verb_reject = _unknown_verb_reject(normalized["verb"], reg)
    if verb_reject:
        return IntentCheckResult(None, (verb_reject,), ())

    ref_rejects = _ref_rejects(normalized, ref_resolver)
    if ref_rejects:
        return IntentCheckResult(None, tuple(ref_rejects), ())

    questions = _questions_for_under_specified(normalized)
    if questions:
        return IntentCheckResult(normalized, (), questions[:1])

    return IntentCheckResult(normalized, (), ())
